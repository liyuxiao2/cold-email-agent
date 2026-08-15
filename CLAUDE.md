# Cold Email Agent — System & Architecture Guide

Autonomous cold email outreach system: discovers early-stage startups from directory listing pages via Firecrawl, researches each company and a pool of its emailable contacts using DuckDuckGo (keyless web search), web scraping, Hunter Domain Search, and a provider-agnostic LLM layer (Groq + Google Gemini with automatic failover), generates personalized email drafts pausing in a human review queue, and sends approved drafts via the Gmail API.

**Two-level, multi-tenant data model (Stack 1b).** `companies` (+ `research`,
`company_contacts`) are GLOBAL — discovered and researched once, admin-populated,
shared by every user. `outreach` (+ `drafts`) is PER-USER — one user's attempt
to reach one company through one contact. A shared company pool with per-user
outreach means the same founder is not necessarily emailed by every user:
`company_contacts` is a pool (from Hunter Domain Search) precisely so different
users can be routed to different eligible contacts at the same company
("contact spreading") instead of everyone hitting the same inbox. See the
Database Schema section below and `docs/architecture-flow.md` for the full
model and Mermaid diagrams.

---

## ☁️ Production Architecture (Google Cloud + Vercel)

The entire production stack runs 24/7 in Google Cloud and Vercel:

```
                          ┌────────────────────────┐
                          │   Vercel Production    │
                          │   Next.js Dashboard    │
                          │ cold-email-*.vercel.app│
                          └───────────┬────────────┘
                                      │ HTTPS / REST
                                      ▼
             ┌──────────────────────────────────────────────────┐
             │       Google Cloud Run (cold-email-backend)       │
             │           Region: us-central1 (2 vCPU, 2GiB)      │
             │                                                  │
             │  ┌───────────────────────┐  ┌─────────────────┐  │
             │  │   FastAPI Web Server  │  │  Celery Worker  │  │
             │  │   (uvicorn on :8080)  │  │  + Celery Beat  │  │
             │  └──────────┬────────────┘  └────────┬────────┘  │
             └─────────────┼────────────────────────┼───────────┘
                           │                        │
             ┌─────────────┴──────────┐   ┌─────────┴──────────┐
             │  Cloud SQL PostgreSQL  │   │ Memorystore Redis  │
             │  Instance:             │   │ Instance:          │
             │  cold-email-db         │   │ cold-email-redis   │
             │  (PostgreSQL 16)       │   │ (10.110.4.35:6379) │
             └────────────────────────┘   └────────────────────┘
```

---

## 🌐 Live URLs & Endpoints

| Component | URL / Endpoint | Description |
|---|---|---|
| **Frontend Dashboard** | [https://cold-email-puce-nine.vercel.app](https://cold-email-puce-nine.vercel.app) | Live Next.js review deck, approvals & company explorer |
| **Backend REST API** | [https://cold-email-backend-426138953095.us-central1.run.app](https://cold-email-backend-426138953095.us-central1.run.app) | Cloud Run FastAPI backend |
| **Health Check** | [`/api/health`](https://cold-email-backend-426138953095.us-central1.run.app/api/health) | API & Cloud SQL database connectivity check — PUBLIC, no auth (Cloud Run's health check calls it) |
| **Pipeline Stats** | [`/api/pipeline/stats`](https://cold-email-backend-426138953095.us-central1.run.app/api/pipeline/stats) | Two dicts: `companies` (by `research_status`, GLOBAL) and `outreach` (by `status`, filtered to the caller) |
| **Company Pool** | `GET /api/companies` | Read-only global company pool (every user sees the same rows) — the admin-populated set a user selects from |
| **Review Queue** | [`/api/outreach/drafts`](https://cold-email-backend-426138953095.us-central1.run.app/api/outreach/drafts) | THIS user's drafted outreach rows pending human review |
| **List Outreach** | `GET /api/outreach` | THIS user's outreach rows, paginated/filtered/searched |
| **Trigger Discovery** | `POST /api/pipeline/discovery` | Enqueues a new startup discovery sweep (admin) |
| **Trigger Drafting** | `POST /api/pipeline/drafting` | Enqueues a batch sweep drafting emails for queued outreach rows (admin) |
| **Requeue Research** | `POST /api/pipeline/research` | Re-dispatches research for companies stuck in `found` (recovers orphaned companies; admin) |
| **Approve Outreach** | `POST /api/outreach/{id}/approve` | Approves THIS user's draft and dispatches `logistics_task` (Gmail send); 404 if the row isn't the caller's |
| **Reject Outreach** | `POST /api/outreach/{id}/reject` | Marks THIS user's outreach rejected with optional notes; 404 if the row isn't the caller's |
| **Regenerate Draft** | `POST /api/outreach/{id}/regenerate` | Resets THIS user's outreach to `queued` and triggers re-drafting; 404 if the row isn't the caller's |
| **List DLQ** | `GET /api/dlq` | Lists dead-lettered (terminally-failed) tasks awaiting retry |
| **Retry DLQ** | `POST /api/dlq/retry` | Re-dispatches dead-lettered tasks (optional `?stage=research\|drafting\|logistics`); resets each row and clears it (admin) |
| **Google Login** | `GET /api/auth/google/login` | Returns `{authorize_url}` for the consent screen (carries a signed CSRF state nonce) |
| **OAuth Callback** | `GET /api/auth/google/callback` | Verifies state, exchanges the code, sets the session cookie, redirects to `FRONTEND_URL` |
| **Current User** | `GET /api/auth/me` | `{id, email, name, picture_url, role, gmail_connected}` — the only source of client-side auth state |
| **Logout** | `POST /api/auth/logout` | Clears the session cookie |

**Every other route above now requires a session cookie**; `POST /api/pipeline/*`
additionally requires `role = 'admin'` (`require_admin`).

---

## 🔐 Auth & the Frontend Shell

Google Sign-In is the only way in. One consent flow yields identity *and* Gmail
send capability; the callback is the only place a Google refresh token is
written, and it is Fernet-encrypted before it touches the database.

- **Session:** an HS256 JWT in an `httpOnly` cookie, so JavaScript cannot read
  it. The frontend never parses the cookie — auth state comes exclusively from
  `GET /api/auth/me`. Cross-origin means `credentials: 'include'` on every
  request and an explicit `CORS_ORIGINS` list — `["*"]` is rejected outright
  because browsers refuse a wildcard origin alongside `allow_credentials=True`,
  which would silently break cookie-based sessions.
- **Admin seeding:** `scripts/seed_admin.py` runs from `scripts/start.sh` on
  every container boot. It is idempotent — creates the `ADMIN_EMAIL` user if
  absent or promotes it to `role='admin'` if it already exists — and is
  guarded so a failed seed cannot prevent the container from starting.
- ⚠️ **`ENCRYPTION_KEY` is unrecoverable.** It's the Fernet key protecting every
  stored Gmail refresh token. Losing or rotating it makes all of those tokens
  undecryptable and forces every user to re-consent through Google Sign-In.
  Generate it once and back it up somewhere durable *before* any user signs in.
- **Frontend layout** (`frontend/`):
  - `lib/api.ts` — every backend call goes through one `request<T>()` helper
    that owns `credentials: 'include'`, `cache: 'no-store'`, and the
    401 → `/login` redirect. One place to get right instead of fifteen.
  - `lib/auth.tsx` — `AuthProvider` + `useAuth() -> {user, loading, logout}`.
    The only React context in the app; it holds auth state and nothing else.
  - `app/login/page.tsx` — sign-in card; `useSearchParams()` sits behind a
    `<Suspense>` boundary so the route still prerenders.
  - `app/page.tsx` — container: auth gate, **all** shared dashboard state
    (`stats`, `draftQueue`, `allCompanies`, `activeTab`, `loading`, `actionLoading`,
    trigger flags, `notification`, and the explorer's `statusFilter` /
    `searchQuery`) and **all** fetching. No state library, no data-fetching
    library. The explorer filters were pulled back up from `CompanyExplorer` into
    `page.tsx` so they survive a tab switch instead of resetting.
  - `components/{PipelineStats,ReviewDeck,CompanyExplorer,AdminPanel}.tsx` —
    presentational, props-only, explicitly typed. Each keeps just its own
    local UI state — `copiedId` and the reject-modal text — nothing more.
    `ReviewDeck` shows which contact each draft is addressed to (name,
    position, email) — the visible payoff of a contact pool over a single
    `founder_email`.
  - `AdminPanel` renders only when `user.role === 'admin'`. **Cosmetic only** —
    `require_admin` on the backend is the real boundary.

---

## 📦 GCP Infrastructure Inventory

| Resource | Service | Identifier / Connection | Specs |
|---|---|---|---|
| **Project** | Google Cloud Project | `cold-email-490016` (Number: `426138953095`) | Active billing |
| **Compute / API / Worker** | Cloud Run | `cold-email-backend` | 2 vCPU, 2 GiB RAM, `min-instances=1`, direct VPC egress |
| **Database** | Cloud SQL | `cold-email-db` (`34.121.96.205`) | PostgreSQL 16 Enterprise, database: `cold_email` |
| **Task Queue Broker** | Memorystore for Redis | `cold-email-redis` (`10.110.4.35:6379`) | 1 GB Basic tier, `us-central1` |
| **Container Registry** | Artifact Registry | `us-central1-docker.pkg.dev/cold-email-490016/cold-email-repo` | Multi-stage Docker images built with `uv` |
| **Logs** | Cloud Logging | `resource.labels.service_name="cold-email-backend"` | Real-time Uvicorn + Celery worker log streaming |

---

## ⚙️ Worker Pipeline Architecture

1. **Discovery (`cold_email.workers.discovery.discovery_task`)**:
   - Scrapes startup directories (e.g. `startups.gallery/categories/industries/*`) via Firecrawl structured extraction.
   - Deduplicates (by `company_name`, protecting the GLOBAL pool — a duplicate company would give two users different contact pools for the same business) and saves new rows into the `companies` table (`research_status = 'found'`).
   - Automatically enqueues `research_task.delay(company_id)` for each new company.
   - Also scheduled via Celery Beat every Monday at 8:00 AM.

2. **Research (`cold_email.workers.research.research_task`)**:
   - Resolves the official company website via DuckDuckGo (`ddgs`, keyless) — candidates scored by `select_best_url` (aggregator blocklist + slug match). Non-fatal search errors propagate so the task retries instead of terminally failing the company.
   - Scrapes `/about`, `/team`, and homepage content using BeautifulSoup, falling back to Firecrawl.
   - Calls the LLM via `generate_json` (see provider-agnostic layer below) to extract founder name, tech stack, recent news, and an interest hook.
   - **Contact discovery (Hunter Domain Search):** fetches every contact at the resolved domain via `find_contacts` and classifies each one with `classify_contacts` into a `ClassifiedContact` (`is_founder`, `eligible`) — a pool in `company_contacts`, not a single `founder_email`. A shared company pool otherwise means every user emails the same person; the pool is what lets different outreach rows route to different people at the same company ("contact spreading"). **Every** contact is saved via `save_contacts`, eligible or not, so loosening the eligibility rule later can re-classify stored rows instead of re-spending Hunter credits.
   - **Fail-fast gate:** `has_eligible_contact` checks whether at least one saved contact is `eligible`. None eligible → the company is **dead-lettered at research** (`fail_company`, `ERR_NO_ELIGIBLE_CONTACTS`, stage `research`) and `research_status = 'failed'`, so it never reaches drafting. Otherwise `research_status = 'researched'`.
   - Recoverable: `POST /api/pipeline/research` re-dispatches research for companies stuck in `found` (discovery only enqueues research for brand-new companies).

3. **Drafting Sweep (`cold_email.workers.drafting.drafting_task`)**:
   - Batch sweep: queries the `pending_drafts` database view for every `outreach` row currently `status = 'queued'` (joined to its company, contact, and research).
   - **Temporary bridge — delete in Stack 3.** Nothing creates `outreach` rows until Stack 3 ships the pool-selection UI (`POST /api/outreach`), so `bridge_queue_admin_outreach()` runs at the top of every sweep and queues an `outreach` row for the admin account over every `researched` company that doesn't already have one — replicating pre-split behaviour (the admin drafts everything researched) so the pipeline doesn't silently stop. **Stack 3 removes this function and its call site** once real user selection exists.
   - **Template-driven, not freeform.** A fixed candidate-outreach template (`prompts/email_template.py`) owns structure/tone; the LLM (via `generate_json` with the `EmailDraftContext` schema) fills only the *contextual slots* — subject, a company-interest phrase, an admiration detail, and the 3 most-relevant experience bullets tailored per company from `sender_profile.PROFILE.experience_pool`. `assemble_email` fills the template (`fill_template` raises on any unfilled `{{token}}`) and renders HTML + a plain-text fallback (`helpers/html_builder.py`). A missing contact email or empty model output → `fail_outreach` (`ERR_NO_CONTACT_EMAIL` / `ERR_EMPTY_DRAFT`), terminal for that one row only — one bad row never aborts the sweep. Calls paced under the free-tier limit.
   - Creates a **multipart** Gmail draft via `create_draft(to, subject, body, html=..., attachment_path=...)` (plain fallback + rich HTML with bold, a bullet list, clickable GitHub/LinkedIn links, and the résumé PDF attached) and saves `gmail_draft_id`.
   - Advances the outreach row to `status = 'drafted'` (held in review queue).
   - Scheduled via Celery Beat to sweep every 15 minutes.

4. **Logistics (`cold_email.workers.logistics.logistics_task`)**:
   - Event-driven per outreach row: triggered when a human clicks **Approve** in the frontend (`POST /api/outreach/{id}/approve`).
   - Sends the stored Gmail draft via Gmail API (`send_draft`).
   - Advances the outreach row to `status = 'sent'`.

---

## 🤖 Provider-Agnostic LLM Layer (`cold_email.workers.shared.llm`)

Every model call goes through `generate_json(system, prompt, schema) -> str`. The
narrow waist hides provider specifics; swapping models is just editing
`settings.model_fallback_chain`.

- **`_provider_for(model)`** routes a model name to its adapter — `gemini*` → `GeminiProvider`, `llama*` → `GroqProvider`, else `ValueError` (fail loud on a typo).
- **Fallback:** `generate_json` walks the chain in order; a model that returns `429` (quota exhausted) or `404` (retired/unavailable) is skipped and the next is tried. Any other error re-raises immediately; if the whole chain is exhausted the last error propagates so the Celery task retries later.
- **Chain** is ordered most-generous-first (per-model free-tier RPM/RPD) and can mix providers, e.g. `["llama-3.3-70b-versatile", "gemini-3.5-flash-lite"]`. Override via the `MODEL_FALLBACK_CHAIN` env var (JSON array); empty falls back to `[MODEL_NAME]`.
- Structured output: Gemini uses native `response_schema`; Groq (no schema binding) injects the JSON schema into the system prompt with `json_object` mode.

---

## ☠️ Dead-Letter Queue (`dead_letter` table)

Every terminal failure funnels through one choke point (`workers/shared/errors.py`)
with **two entry points** for the two levels of the tenancy split — one function
with a nullable `company_id`/`outreach_id` pair would push the branch into every
call site and make the `dead_letter_one_level` CHECK reachable by accident:

- **`fail_company(company_id, reason, *, stage, task_name)`** — a GLOBAL failure:
  nobody can email this company (research found no eligible contact). Sets
  `companies.research_status = 'failed'`.
- **`fail_outreach(outreach_id, reason, *, stage, task_name)`** — a PER-USER
  failure: this user's draft or send broke. Sets `outreach.status = 'failed'`.

Both write a `dead_letter` row via `record_dead_letter`. `dead_letter` has TWO
nullable FKs, `company_id` and `outreach_id`, with a CHECK (`dead_letter_one_level`)
that exactly one is set — research failures are company-level, drafting/logistics
failures are outreach-level, and collapsing both into one FK would lose that
distinction. A permanently-failed row is visible on the company or outreach row
*and* independently retryable.

- **Producers:** research (`fail_company`, no eligible contact — `ERR_NO_ELIGIBLE_CONTACTS`), drafting (`fail_outreach`, no contact email or empty model output), logistics (`fail_outreach`, no Gmail draft — `ERR_NO_GMAIL_DRAFT`).
- **Retry:** `POST /api/dlq/retry` resets each row to its stage's input state — `research` → `companies.research_status = 'found'`, `drafting` → `outreach.status = 'queued'`, `logistics` → `outreach.status = 'approved'` — re-dispatches the worker, and deletes the row. A task that fails again is re-written to the DLQ by the same choke point, so the queue self-cleans.
- **Inspect:** `GET /api/dlq` lists rows with the joined company name (via EITHER FK — `company_id` directly for a research failure, or `outreach_id → outreach.company_id` for drafting/logistics), stage, error, and retry count.
- On first boot after migration `004`, `start.sh` provisions the table via idempotent `Base.metadata.create_all`.

---

## 🗄️ Database Schema & Views

Two-level status vocabulary — GLOBAL (`companies.research_status`) and PER-USER
(`outreach.status`) are separate state machines with no shared values except
`failed`:

- `companies.research_status`: `found` → `researched`, or `found` → `failed` (no eligible contacts).
- `outreach.status`: `queued` → `drafted` → `approved` → `sent`, or `drafted` → `rejected`, or `queued`/`drafted` → `failed`.

Tables:

- **`companies`**: The GLOBAL pool — discovered once, researched once, reused by every user. Company info (`company_name`, `company_url`, `linkedin_url`, `founder_name`, `funding_stage`, `headcount`, `industry`), `research_status`, `error_msg`. No per-user state.
- **`company_contacts`**: One emailable person at a company, from Hunter Domain Search — a pool, not a single `founder_email`, so a shared company pool doesn't mean every user emails the same person. `email`, `first_name`, `last_name`, `position`, `seniority`, `department`, `confidence` (Hunter 0-100), `is_founder`, `eligible`. Ineligible contacts are stored too (see the Research bullet above).
- **`outreach`**: The PER-USER half — one user's attempt to reach one company through one contact. `user_id`, `company_id`, `contact_id` (nullable, `SET NULL` on contact delete), `status`, `scheduled_send_at` (NULL = send immediately; wired for Stack 4), `error_msg`. `UNIQUE(user_id, company_id)` — a user targets a company at most once; two different users targeting the same company is expected.
- **`research`**: Tech stack JSONB, recent news, value proposition hook, raw scraped content. FK's to `companies` (GLOBAL, not per-outreach).
- **`drafts`**: Subject line, generated email body, `gmail_draft_id`, version, reviewer notes. FK's to `outreach` (PER-USER).
- **`dead_letter`**: Terminally-failed tasks — nullable `company_id` **and** `outreach_id` (CHECK: exactly one set), `task_name`, `stage`, `error_msg`, `retry_count` (see DLQ above).
- **`leads_legacy`**: The pre-split `leads` table, renamed (not dropped) by migration `006`. `companies.id` reuses `leads.id` verbatim, so every FK remap onto the new tables is a pure column rename. Kept as a rollback safety net until this deploy is proven; a follow-up migration drops it.
- **`profiles`** (migration `007`): Per-user sender identity — `user_id` is the PRIMARY KEY (one profile per user, structural not a unique constraint), `name`, `intro`, `linkedin`, `github`, `website`, `experience_pool` (JSONB list), `company_links` (JSONB dict), `resume_pdf` (BYTEA, `STORAGE EXTERNAL` — see below), `resume_filename`, `resume_text`, `parsed_at`. Replaces the old compiled-in `sender_profile.PROFILE` plus the committed `resume.txt`/`resume.pdf`. All reads/writes of `resume_pdf` go through `cold_email/resume_store.py` — no other module touches those bytes. `SenderProfile.from_row` builds the in-memory dataclass from a `profiles` row.
- **`pending_drafts` View**: Distinct `outreach` rows in `queued` status, joined to their company, contact, and latest research.
- **`pending_sends` View**: Distinct `outreach` rows in `approved` status (and due, per `scheduled_send_at`), joined to their contact email and latest draft.
- **`available_contacts` View**: Every eligible `company_contacts` row with its `use_count` (how many `outreach` rows already reference it) — exposes the count rather than filtering on a cap, since baking a cap `K` into the view would make changing that business rule require a migration. Stack 3's per-contact cap selection reads this.

---

## 🔑 Environment Variables & Secrets

```bash
# Database (Cloud SQL Unix socket in Cloud Run, TCP locally)
DATABASE_URL=postgresql+asyncpg://cold_email:ColdEmailAdmin2026Secure!@34.121.96.205:5432/cold_email

# Redis (Memorystore private IP in Cloud Run, localhost locally)
CELERY_BROKER_URL=redis://10.110.4.35:6379/0
CELERY_RESULT_BACKEND=redis://10.110.4.35:6379/1

# AI & Scraping APIs
FIRECRAWL_API_KEY=fc-...              # scraping (research fallback)
GEMINI_API_KEY=AQ...                 # Gemini provider
GROQ_API_KEY=gsk_...                 # Groq provider (llama models in the fallback chain)
HUNTER_API_KEY=...                   # Hunter.io Domain Search (contact-pool discovery in research)
# URL discovery uses DuckDuckGo (ddgs) — keyless, no API key needed.
# Optional: MODEL_FALLBACK_CHAIN=["llama-3.3-70b-versatile","gemini-3.5-flash-lite"]
# Optional: MODEL_NAME=gemini-flash-latest   (default single model when chain unset)

# Gmail API — OAuth2 refresh-token flow (headless send from a single mailbox).
# Mint these once with: uv run python scripts/gmail_auth.py --client-secret <client.json>
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
GMAIL_REFRESH_TOKEN=...
GMAIL_SENDER_EMAIL=...

# Sender identity is code, not config — see cold_email/sender_profile.py (PROFILE).

# Auth (Google Sign-In + per-user sessions)
SESSION_SECRET=...                   # HS256 signing key for the session JWT
ENCRYPTION_KEY=...                   # Fernet key (44-char urlsafe base64) for refresh tokens at rest
GOOGLE_REDIRECT_URI=...              # must exactly match the Google console entry
FRONTEND_URL=http://localhost:3000   # where the OAuth callback redirects back to
ADMIN_EMAIL=...                      # seeded with role='admin' on boot
COOKIE_SECURE=true                   # false only for local http development
CORS_ORIGINS=["http://localhost:3000"]  # explicit list, never ["*"] (cookies need allow_credentials)

# Frontend Vercel Config
NEXT_PUBLIC_API_URL=https://cold-email-backend-426138953095.us-central1.run.app
```

---

## 💻 Local Development vs. Cloud Commands

### Running Locally with Docker:
```bash
# Start local DB & Redis
make up

# Run database migrations
uv run python -c "from cold_email.database import engine; ..."

# Start Celery Worker
make worker

# Start Celery Beat
make beat

# Start FastAPI API
make server

# Start Frontend
cd frontend && npm run dev
```

### Deploying Updates to Cloud Run:

**PRE-DEPLOY (one-time, before the tenancy-model image ever ships to
production): apply migration 006.** Nothing in the container or its startup
path runs `migrations/006_multi_tenant_schema.sql` — `scripts/start.sh` only
runs `Base.metadata.create_all` (which creates the new `companies` /
`company_contacts` / `outreach` tables, but EMPTY, and provisions no data) and
`scripts/apply_views.py` (which re-declares `pending_drafts` / `pending_sends`
/ `available_contacts` against whatever tables already exist) plus
`scripts/apply_storage.py` (which sets `profiles.resume_pdf`'s TOAST storage
strategy to `EXTERNAL` — another thing `Base.metadata.create_all` cannot
express, same class of gap as the views). Skipping 006 is
the DEFAULT outcome of a normal deploy, not an edge case, and it "succeeds"
quietly:
- `create_all` makes the new tables, empty. The old `leads` table and its data
  are untouched and never migrated.
- The stale pre-migration views (if this is the first deploy off `leads`) or
  the newly-declared post-migration views (if `views.sql` ran against an
  un-migrated schema) survive either way.
- `GET /api/health` only checks DB connectivity, so it still returns 200 —
  Cloud Run's health check passes and cuts traffic over to the new revision.
- The drafting sweep then raises
  `TypeError: PendingDraft() got an unexpected keyword argument 'lead_id'`
  (or the equivalent "relation does not exist" / missing-column error) every
  15 minutes, and every lead/company sits stranded — nothing is ever drafted
  or sent, with no user-visible error anywhere in the dashboard.

Apply it by hand, before rolling out this stack's image, with
`ON_ERROR_STOP=1` so a failing statement stops `psql` instead of continuing
past it:
```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/006_multi_tenant_schema.sql
```

Before running it, check whether the migration's drafts-orphan guard (the
`RAISE EXCEPTION ... refusing to delete draft bodies` check) would fire. A
non-empty result means at least one `leads` row is sitting outside the
statuses the `outreach` backfill maps directly, has a draft row attached (most
often via the old regenerate-then-never-redrafted path,
`POST /api/leads/{id}/regenerate`, which reset `status` to `'researched'` but
left the draft in place) — the migration now backfills these into `outreach`
at `status = 'drafted'` rather than aborting, but it's worth knowing what's
about to move before running it against production:
```sql
SELECT l.status, count(*) FROM drafts d JOIN leads l ON l.id = d.lead_id
WHERE l.status NOT IN ('drafted','approved','sent','rejected')
  AND NOT (l.status='failed' AND l.founder_email IS NOT NULL)
GROUP BY 1;
```

```bash
# 1. Build and push updated container to Artifact Registry
gcloud builds submit --tag us-central1-docker.pkg.dev/cold-email-490016/cold-email-repo/cold-email-backend:latest .

# 2. Deploy to Cloud Run
gcloud run deploy cold-email-backend \
  --image="us-central1-docker.pkg.dev/cold-email-490016/cold-email-repo/cold-email-backend:latest" \
  --platform=managed \
  --region=us-central1 \
  --memory=2Gi \
  --cpu=2 \
  --min-instances=1 \
  --no-cpu-throttling
```

### Viewing Real-Time Worker Logs on GCP:
```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="cold-email-backend"' --limit=50 --format="value(textPayload)"
```
