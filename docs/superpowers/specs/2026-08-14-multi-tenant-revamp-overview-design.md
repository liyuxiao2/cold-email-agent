# Multi-Tenant Revamp — Overview & Decision Record

_Date: 2026-08-14_
_Status: approved, decomposed into 5 stacks_

## Context

The Cold Email Agent today is a single-tenant tool. One person's identity is
compiled into the codebase: `sender_profile.PROFILE` is a frozen module-level
dataclass, `settings.gmail_refresh_token` is one mailbox, and `resume.txt` /
`resume.pdf` are files in the repo. Every lead in the database belongs,
implicitly, to that one person.

The goal is a commercial multi-user product:

- Users log in with Google.
- Each user attaches their own résumé and sends from their own Gmail.
- Companies and their research live in **one global pool**, discovered and
  researched once by an admin, and reused by every user.
- Non-admin users **cannot** trigger discovery or research — that work is
  already done for them. They browse the pool, draft, approve, and send/schedule.

## Why this is five specs, not one

The request spans six independent subsystems: authentication, per-user identity
(résumé + Gmail OAuth), the global/per-user data split, role gating, scheduling,
and billing. Billing was explicitly deferred. The remaining five are ordered by
dependency and each is independently shippable:

| Stack | Branch | Ships |
|---|---|---|
| **1a** | `feat/tenancy-auth` | `users` table, Google OAuth login, session cookie, `role` gating, CORS correctness |
| **1b** | `feat/tenancy-data-model` | `companies` / `company_contacts` / `outreach` split, Hunter Domain Search, migration + backfill, full rename, docs & diagrams |
| **2** | `feat/sender-identity` | `profiles` table, résumé upload → parse → confirm, `resume_store`, per-user Gmail credentials |
| **3** | `feat/pool-and-drafting` | Pool browser, contact selection + global cap, on-demand per-user drafting, Redis token bucket, quota, BYOK |
| **4** | `feat/scheduling` | Per-email scheduled sends, daily cadence, due-send Beat scanner |

1a and 1b are split because authentication does not depend on the schema split,
and combining them produces a single unreviewably large diff (the rename alone
touches ~50 files and ~800 references).

Each stack is a separate PR, stacked in order. `gt submit` does not work in this
repo — use `git push` + `gh pr create --base <parent-branch>`.

---

## Decision record

Every decision below was explicitly chosen during brainstorming. Recorded here
so no stack re-litigates them.

### Authentication — Google Sign-In only

One consent screen yields both identity (`openid email profile`) and the Gmail
send scope (`gmail.compose`). No password hashing, resets, or email
verification. The pipeline already assumes users send from Gmail, so supporting
non-Gmail signups would let people register accounts that structurally cannot
send.

**Consequence:** the existing Google Cloud OAuth client is reused for login —
it needs the `openid email profile` scopes and a web redirect URI added.
`gmail_client_id` / `gmail_client_secret` stay app-level settings.

### Billing — deferred

All users on one free tier. Nothing in this design blocks Stripe later; a `plan`
column is additive. Per-user *quotas* are still in scope (Stack 3) because
without them one user can exhaust the shared LLM free tier for everyone.

### Company pool — fully shared, no claiming

`outreach` is a per-user row; uniqueness is `(user_id, company_id)`. Two users
may target the same company. No claim/lock mechanism, no expiry logic.

**But not the same person** — see contact spreading below.

### Contact spreading — the pool is contacts, not companies

A fully shared pool naively means every user emails the same `founder_email`,
so one founder receives N near-identical emails from N different senders. That
reads as a spam farm and is the single biggest reputational risk in the product.

Four decisions fix it:

1. **Hunter Domain Search replaces Email Finder.** `/v2/email-finder` takes
   name + domain and returns exactly *one* address — it structurally cannot
   produce a pool. `/v2/domain-search` takes a domain and returns many contacts
   with `first_name`, `last_name`, `position`, `seniority`, `department`,
   `confidence`, and `type`. Research stores all of them in `company_contacts`.
2. **Selection is least-globally-contacted, tie-broken on confidence.** A
   `COUNT` of `outreach` rows per contact picks the least-used address. Chosen
   over random because it distributes evenly *by construction* and is
   deterministic, therefore unit-testable.
3. **Eligibility is decision-makers and hiring roles only.** Founder, C-level,
   VP/Head/Director of Engineering, and recruiting/talent. `type='generic'`
   (`info@`, `support@`, `hello@`) is excluded. This keeps the existing
   founder-flavored email template honest with zero prompt changes.
4. **A global per-contact cap `K`.** A contact may be emailed by at most `K`
   users ever (start at 3, configurable). When every eligible contact at a
   company is capped, the company leaves everyone's pool. Broad admin discovery
   is therefore the growth lever — which the weekly 28-industry sweep already
   provides.

### Existing production data — migrated, not wiped

Researched companies and Hunter-verified addresses are the app's initial value:
a new user signs up to a pre-populated pool. Liyu Xiao becomes admin user #1 and
inherits all existing drafts and sent history.

**The migration's key trick:** `companies.id` reuses `leads.id` verbatim. Because
the UUIDs carry over, `research.lead_id` → `research.company_id` is a column
rename with no remapping, and no ID translation table is needed anywhere.

⚠️ **Accepted data loss:** `find_email` receives a Hunter `score` but nothing
persists it — `should_accept_email` reads it and discards it. Backfilled
contacts therefore get a sentinel `confidence` of `MIN_EMAIL_SCORE` (25), since
they demonstrably passed that gate. New contacts store the real value. Legacy
contacts will consequently rank pessimistically in the confidence tie-break.

### Profile setup — parse the PDF, LLM extracts, user confirms

On upload: `pypdf` → raw text → one `generate_json` call with a `ResumeProfile`
schema extracts name, intro, experience bullets, and links into a form the user
reviews and edits. Reuses the existing provider-agnostic LLM layer. The
alternative — an 8-field manual form including writing your own intro sentence —
is a real onboarding drop-off point for a commercial product.

### Résumé storage — Postgres `bytea` behind a `resume_store` module

At ~400KB per user, 1,000 users is ~0.4GB: roughly $0.07/month on Cloud SQL
versus $0.01/month on GCS. Cost is not the deciding factor. `bytea` wins because
the profile row and the PDF commit in **one transaction** — with GCS they are two
systems, and a crash between the blob write and the row commit leaves an orphan
file whose reconciliation you own.

Implementation notes:

- `ALTER COLUMN resume_pdf SET STORAGE EXTERNAL` — PDFs are already compressed,
  so skip Postgres's futile compression attempt.
- Postgres pages are 8KB, so the value is stored out-of-line in the TOAST side
  table with an 18-byte pointer in the heap row. `SELECT id, name FROM profiles`
  never touches the bytes.
- Reads and writes go through `resume_store.get_resume` / `put_resume` so a
  future GCS migration is one implementation swap plus a backfill, not a hunt
  through the drafting worker.
- Hard 5MB upload cap. Cloud SQL disk can grow but **never shrink**, so an
  unbounded upload path permanently inflates the instance.

Revisit GCS at ~5GB total, or the moment multi-file / versioned résumés appear.

### Draft trigger — user selects companies, then "Draft these"

The current global Beat sweep every 15 minutes becomes a **per-user task over a
user's selected set**. Explicit targeting, user-controlled spend, no runaway
background LLM cost. The Beat sweep survives only as a retry/recovery mechanism
for `queued` rows a dispatch dropped.

### Scheduling — per-email datetime *and* daily cadence

One nullable `outreach.scheduled_send_at` column serves both: a datetime picker
writes it directly, and a "10/day at 9am" cadence computes staggered values
across the approved queue. One new Beat task scans for due sends.

**No separate `scheduled` status** — `approved` plus a nullable timestamp covers
it (NULL means send immediately). A `scheduled` state would be derivable from the
column, making it a second source of truth that can disagree.

### Discovery gating — admin-only, no request queue

The weekly sweep already covers 28 industries; users filter the pool instead. No
`discovery_requests` table until users actually ask for one.

### Human review — always mandatory

`drafted` → `approved` stays a human action for every user. An LLM never emails a
stranger from a user's mailbox unreviewed. Scheduling affects only *when* an
approved email goes out, never *whether* a human saw it.

### LLM access — pooled keys by default, optional BYOK, self-hostable

Three paths behind one resolver, `resolve_llm_credentials(user)`:

1. **Default:** the platform's keys (limits to be raised), behind a Redis token
   bucket shared across workers plus a per-user monthly draft quota.
2. **Optional BYOK:** a user may supply their own Groq/Gemini key, stored
   encrypted. It bypasses the platform quota and uses their own limits.
3. **Self-host:** keys via environment variables, exactly as today.

`time.sleep(LLM_MIN_INTERVAL_SECONDS)` is replaced by the token bucket. A `sleep`
paces one worker process; a bucket paces the whole fleet — which is the actual
constraint once N users draft concurrently.

---

## Target schema

```sql
users                                    -- NEW (1a)
  id, google_sub UNIQUE, email UNIQUE, name, picture_url
  role                     'user' | 'admin'
  gmail_refresh_token_enc  BYTEA          -- per-user send identity, Fernet
  gmail_sender_email

companies                                -- was `leads`; global facts only (1b)
  id, company_name, company_url, linkedin_url, founder_name,
  funding_stage, headcount, industry
  research_status          'found' | 'researched' | 'failed'
  error_msg
  -- founder_email is GONE → company_contacts

company_contacts                         -- NEW (1b); one row per domain-search hit
  id, company_id FK
  email, first_name, last_name, position, seniority, department
  confidence INT           -- Hunter 0-100
  is_founder BOOL          -- matched against LLM-extracted founder_name
  eligible   BOOL          -- passed position filter + MIN_EMAIL_SCORE
  UNIQUE (company_id, email)

research                                 -- shape unchanged, re-pointed (1b)
  company_id FK → companies

outreach                                 -- NEW (1b); per-user
  id, user_id FK, company_id FK, contact_id FK
  status              'queued'|'drafted'|'approved'|'sending'|'sent'
                      |'rejected'|'failed'    -- 'sending' added in Stack 4
  scheduled_send_at   TIMESTAMPTZ NULL    -- NULL = send immediately
  error_msg
  UNIQUE (user_id, company_id)

profiles                                 -- NEW (2)
  user_id PK FK, name, intro, linkedin, github, website
  experience_pool JSONB, company_links JSONB
  resume_pdf BYTEA (STORAGE EXTERNAL), resume_filename, resume_text

drafts          outreach_id FK  (was lead_id)
dead_letter     company_id FK NULL, outreach_id FK NULL
                CHECK (company_id IS NOT NULL OR outreach_id IS NOT NULL)
```

### Why the status split lands here

`found` / `researched` / `failed` are global facts about a company →
`companies.research_status`. `drafted` / `approved` / `sent` / `rejected` are
per-user → `outreach.status`. **No status value belongs to both**, which is
strong evidence the split is at the right seam.

`queued` is a new initial state. Because drafting is now on-demand, "Draft these"
writes `outreach` rows at `queued` and the per-user task consumes them. This
preserves the existing view pattern: `pending_drafts` becomes `outreach` at
`queued` joined to company + latest research + contact.

`sending` is the one *derived-looking* state that earns its place, and it arrives
in Stack 4. Celery guarantees at-least-once task delivery, so a send scanner
running every five minutes over rows that only leave the set on success will
eventually dispatch the same row twice. `approved` cannot express "already handed
to a worker." Without that distinction, at-least-once *task* delivery becomes
at-least-once *email* — and a cold email sent twice to a founder cannot be undone.

### Why `dead_letter` has two nullable FKs

Research failures are company-level (global — nobody can email that company).
Drafting and send failures are outreach-level (one user's problem). Forcing both
into one FK means either fabricating outreach rows for research failures, or
losing the distinction between "broken for everyone" and "broken for Alice".
A `CHECK` constraint enforces that exactly one level is populated.

---

## Cross-cutting: full migration of names, docs, and diagrams

Nothing may still describe the single-tenant model when the stacks land.
Measured surface: **~50 files, ~800 `lead` references, 3 SVG diagrams, 4 markdown
documents.**

| Surface | Refs | Work | Stack |
|---|---|---|---|
| `api/routes/leads.py` | 97 | → `outreach.py`, rewritten around user-scoped queries | 1b |
| Workers (research, discovery, drafting, logistics, shared) | ~180 | `lead_id` → `company_id` or `outreach_id` per stage | 1b |
| `tests/` | ~140 | Follow the rename; new auth/tenancy tests | all |
| `frontend/app/page.tsx` | 98 (917 lines) | **Split into components** — already oversized, and login, pool browser, profile, and scheduling are all being added | 1a–4 |
| `frontend/lib/api.ts` | 24 | Auth headers/credentials, new endpoints | 1a–4 |
| `CLAUDE.md` | 41 | Architecture, worker pipeline, schema, env vars — updated **per stack** | all |
| `docs/architecture-flow.md` | 9 | Rewrite for multi-tenant | 1b |
| `README.md`, `docs/DEPLOYMENT.md` | 6 | Multi-tenant setup, new env vars, OAuth client config | 1a, 1b |
| `coreArchitecture.svg`, `pipeline.svg`, `lifcecycle.svg` *(sic)* | — | **Convert to Mermaid** in markdown | 1b |
| `migrations/*.sql` | 23 | Historical — left as-is; new migrations added | — |
| `docs/superpowers/{specs,plans}/2026-04-18-*` | 146 | **Historical record — not retconned**; a pointer to these specs is added | 1b |

Two explicit calls:

- **SVGs → Mermaid.** Hand-authored SVG cannot be meaningfully diffed in a pull
  request. Mermaid in markdown makes diagram changes reviewable.
- **The April spec and plan are not rewritten.** They are a dated record of what
  was true then. Retconning them destroys the project's design history. A
  forward-pointer note is added instead.
- **The `lifcecycle.svg` filename typo** is fixed during the Mermaid conversion.

---

## Testing strategy

Per-stack detail lives in each stack's spec. Cross-cutting rules:

- **Tenancy isolation is a test category, not an assertion.** Every user-scoped
  endpoint gets a test proving user A cannot read or mutate user B's outreach.
  This is the class of bug that is invisible in single-user manual testing and
  catastrophic in production.
- **Role gating gets negative tests.** Non-admin → 403 on discovery and research.
- **The migration is tested against a seeded fixture** resembling production:
  leads in every status, some with `founder_email` and some without, research
  rows, drafts, and dead-letter rows at all three stages.
- **Contact selection and the cap are pure functions over counts**, tested
  directly — this is precisely why deterministic selection was chosen over random.
- No test may depend on network access to Google, Hunter, Firecrawl, or any LLM
  provider. All are mocked at the client boundary, as the existing suite does.

## Out of scope

Stripe/billing; team or organization accounts; email reply tracking and threading;
A/B testing of templates; per-user discovery; a `discovery_requests` queue;
résumé versioning; non-Gmail send providers; GCS blob storage.
