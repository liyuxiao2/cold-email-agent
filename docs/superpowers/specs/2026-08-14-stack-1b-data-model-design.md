# Stack 1b — Tenancy Foundation: Data Model Split & Full Rename

_Date: 2026-08-14_
_Branch: `feat/tenancy-data-model` (base: `feat/tenancy-auth`)_
_Parent spec: [Multi-Tenant Revamp Overview](2026-08-14-multi-tenant-revamp-overview-design.md)_

## Goal

Split `leads` into global company facts and per-user outreach state, replace the
single `founder_email` with a pool of `company_contacts` sourced from Hunter
Domain Search, migrate all production data, and update every name, document, and
diagram that still describes the single-tenant model.

This is the largest stack. It carries the rename: ~50 files, ~800 `lead`
references, 3 diagrams, 4 markdown documents.

## The seam

`leads` conflates two different lifetimes:

- `company_name`, `company_url`, `linkedin_url`, `founder_name`,
  `funding_stage`, `headcount` — **global facts**, true for every user.
- `status`, `error_msg`, and the `drafts` rows hanging off it — **per-user
  outreach state**.

`founder_email` looks global but is neither: it is a *single* address where the
product needs a *pool*, because a fully shared company pool otherwise means one
founder receives an email from every user who ever signs up.

## Data model

```sql
-- migrations/006_multi_tenant_schema.sql

CREATE TABLE companies (
    id              UUID PRIMARY KEY,          -- reuses leads.id verbatim
    company_name    TEXT NOT NULL,
    company_url     TEXT,
    linkedin_url    TEXT,
    founder_name    TEXT,
    funding_stage   TEXT,
    headcount       INT,
    industry        TEXT,
    research_status TEXT NOT NULL DEFAULT 'found',  -- found|researched|failed
    error_msg       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX companies_name_idx   ON companies (company_name);
CREATE INDEX companies_status_idx ON companies (research_status);

CREATE TABLE company_contacts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    first_name  TEXT,
    last_name   TEXT,
    position    TEXT,
    seniority   TEXT,
    department  TEXT,
    confidence  INT  NOT NULL DEFAULT 0,       -- Hunter 0-100
    is_founder  BOOLEAN NOT NULL DEFAULT false,
    eligible    BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, email)
);
CREATE INDEX company_contacts_eligible_idx
    ON company_contacts (company_id) WHERE eligible;

CREATE TABLE outreach (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id        UUID REFERENCES company_contacts(id) ON DELETE SET NULL,
    status            TEXT NOT NULL DEFAULT 'queued',
    scheduled_send_at TIMESTAMPTZ,             -- NULL = send immediately
    error_msg         TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, company_id)
);
CREATE INDEX outreach_user_status_idx ON outreach (user_id, status);
CREATE INDEX outreach_contact_idx     ON outreach (contact_id);
```

Notes on specific choices:

- **`companies.id` has no `DEFAULT`** in the create statement above during
  migration, then gains `DEFAULT gen_random_uuid()`. The backfill supplies IDs
  explicitly from `leads.id`.
- **`company_contacts_eligible_idx` is partial** (`WHERE eligible`). Selection
  and pool queries only ever look at eligible contacts, so indexing the
  ineligible ones wastes space and write throughput.
- **`outreach.contact_id` is `ON DELETE SET NULL`, not `CASCADE`.** If a contact
  is later purged (bounced, GDPR request), the *record that an email was sent*
  must survive — deleting outreach history would let the same person be
  re-emailed by the same user.
- **`outreach_contact_idx` exists specifically for the cap query** in Stack 3:
  `COUNT(*) ... WHERE contact_id = ?` runs for every candidate contact when
  building the pool.

### Altered tables

```sql
ALTER TABLE research     RENAME COLUMN lead_id TO company_id;
ALTER TABLE research     ADD CONSTRAINT research_company_fk
                         FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;

ALTER TABLE drafts       ADD COLUMN outreach_id UUID REFERENCES outreach(id) ON DELETE CASCADE;
-- backfill outreach_id from lead_id, then:
ALTER TABLE drafts       DROP COLUMN lead_id;

ALTER TABLE dead_letter  ADD COLUMN company_id  UUID REFERENCES companies(id) ON DELETE CASCADE;
ALTER TABLE dead_letter  ADD COLUMN outreach_id UUID REFERENCES outreach(id) ON DELETE CASCADE;
-- backfill by stage, then:
ALTER TABLE dead_letter  DROP COLUMN lead_id;
ALTER TABLE dead_letter  ADD CONSTRAINT dead_letter_one_level
    CHECK (company_id IS NOT NULL OR outreach_id IS NOT NULL);

ALTER TABLE leads RENAME TO leads_legacy;   -- NOT dropped
```

`leads` is renamed, never dropped. A bad deploy is then recoverable without
restoring a backup. A follow-up PR drops `leads_legacy` once the deploy is
proven.

### Views

```sql
CREATE OR REPLACE VIEW pending_drafts AS
SELECT DISTINCT ON (o.id)
    o.id AS outreach_id, o.user_id, o.company_id, o.contact_id,
    c.company_name, c.company_url, c.founder_name,
    ct.email AS contact_email, ct.first_name AS contact_first_name,
    ct.position AS contact_position,
    r.raw_content, r.tech_stack, r.recent_news, r.hook
FROM outreach o
JOIN companies c        ON c.id  = o.company_id
JOIN company_contacts ct ON ct.id = o.contact_id
JOIN research r         ON r.company_id = o.company_id
WHERE o.status = 'queued'
ORDER BY o.id, r.created_at DESC;

CREATE OR REPLACE VIEW pending_sends AS
SELECT DISTINCT ON (o.id)
    o.id AS outreach_id, o.user_id,
    ct.email AS contact_email,
    d.gmail_draft_id, d.subject_line, d.body
FROM outreach o
JOIN company_contacts ct ON ct.id = o.contact_id
JOIN drafts d            ON d.outreach_id = o.id
WHERE o.status = 'approved'
  AND (o.scheduled_send_at IS NULL OR o.scheduled_send_at <= now())
ORDER BY o.id, d.created_at DESC;

CREATE OR REPLACE VIEW available_contacts AS
SELECT ct.id AS contact_id, ct.company_id, ct.confidence, ct.is_founder,
       COUNT(o.id) AS use_count
FROM company_contacts ct
LEFT JOIN outreach o ON o.contact_id = ct.id
WHERE ct.eligible
GROUP BY ct.id;
```

`available_contacts` deliberately exposes `use_count` rather than filtering on
the cap `K`. Baking `K` into a view means changing a business rule requires a
migration; the application applies `K` in its `WHERE` clause instead.

`pending_sends` already carries the `scheduled_send_at` clause even though
nothing sets that column until Stack 4. Writing it now means Stack 4 adds no view
migration, and `NULL` scheduling behaves as "send immediately" from day one.

### The status split, restated

| Old `leads.status` | New home |
|---|---|
| `found` | `companies.research_status = 'found'` |
| `researched` | `companies.research_status = 'researched'` |
| `failed` (at research) | `companies.research_status = 'failed'` |
| `drafted` | `outreach.status = 'drafted'` |
| `approved` | `outreach.status = 'approved'` |
| `sent` | `outreach.status = 'sent'` |
| `rejected` | `outreach.status = 'rejected'` |
| `failed` (at drafting/send) | `outreach.status = 'failed'` |
| — (new) | `outreach.status = 'queued'` |

No value appears on both sides, which is strong evidence the split is at the
right seam. `failed` is the one word appearing twice, and the two occurrences are
genuinely different failures: "nobody can email this company" versus "this user's
draft broke."

## Migration & backfill

Run as one transactional SQL migration. The ordering matters — `outreach` cannot
be populated before `users`, and `drafts.outreach_id` cannot be populated before
`outreach`.

1. `companies` ← `INSERT ... SELECT` from `leads`, carrying `id` verbatim.
   `research_status` = `'researched'` when `leads.status` is any of
   `researched|drafted|approved|sent|rejected`, `'failed'` when `leads.status`
   is `failed` **and** the lead has no `founder_email`, else `'found'`.
2. `company_contacts` ← one row per lead with a non-null `founder_email`:
   `email` = `leads.founder_email`, names split from `founder_name`,
   `is_founder = true`, `eligible = true`, `confidence = 25` (`MIN_EMAIL_SCORE`).
3. `research.lead_id` → `research.company_id` (rename only; IDs already match).
4. `outreach` ← one row per lead whose `status` is
   `drafted|approved|sent|rejected`, or `failed` *with* a `founder_email`:
   `user_id` = the admin user, `company_id` = `leads.id`,
   `contact_id` = the contact seeded in step 2, `status` = `leads.status`.
5. `drafts.outreach_id` ← joined via `lead_id` → `outreach.company_id`.
6. `dead_letter` ← rows with `stage='research'` get `company_id = lead_id`; rows
   with `stage` in `drafting|logistics` get the matching `outreach_id`.
7. `leads` → `leads_legacy`.

### The trick that makes this cheap

**`companies.id` reuses `leads.id`.** Because the UUIDs carry over,
`research.lead_id` → `company_id` is a pure rename, `dead_letter` research rows
map directly, and no ID translation table exists anywhere in the migration.

### Failure modes the migration must handle

| Case | Handling |
|---|---|
| Lead in `found` with no research | `companies` row only; no contact, no outreach |
| Lead `failed` at research, no email | `research_status='failed'`; no outreach row |
| Lead `failed` at drafting, has email | `research_status='researched'` + `outreach.status='failed'` |
| Two leads sharing a `founder_email` at the same company | `UNIQUE(company_id, email)` — impossible within one company; across companies it is fine and expected |
| `founder_name` is a single word or empty | `first_name` = the word or NULL, `last_name` NULL. Do not fabricate. |
| `dead_letter` row whose lead has no outreach row | `stage='research'` → `company_id`; any other stage is a data inconsistency → log and set `company_id` |
| No admin user exists | **Migration aborts.** Stack 1a's seed must have run. |

⚠️ **Accepted loss:** Hunter's `score` was never persisted (`find_email` returns
it, `should_accept_email` reads and discards it). Backfilled contacts get
`confidence = 25`. Stack 3's confidence tie-break will therefore rank legacy
contacts pessimistically — correct-ish, since 25 is the floor they cleared.

## Hunter: Email Finder → Domain Search

`find_email` calls `/v2/email-finder`, which takes name + domain and returns
exactly one address. It cannot produce a pool. It is replaced.

`helpers/email_finder.py` → `helpers/contact_finder.py`:

| Function | Purpose |
|---|---|
| `domain_from_url(url)` | **kept verbatim** — already correct and tested |
| `looks_like_person_name(name)` | **kept** — now used to decide whether `founder_name` can be matched against results, not to gate the API call |
| `find_contacts(domain) -> list[HunterContact]` | `/v2/domain-search`; returns every result |
| `classify_contacts(contacts, founder_name) -> list[Contact]` | Sets `is_founder` and `eligible` |
| `has_eligible_contact(contacts) -> bool` | Replaces `should_accept_email` as the research fail-fast gate |

### Eligibility rules

A contact is eligible when **all** hold:

1. `type != 'generic'` — excludes `info@`, `support@`, `hello@`, `careers@`.
2. `confidence >= MIN_EMAIL_SCORE` (25) — unchanged threshold, now per-contact.
3. `position` matches a decision-maker or hiring pattern, **or** `is_founder`.

The position patterns live in `research/constants.py` as
`DECISION_MAKER_PATTERNS`, matched case-insensitively as substrings:

```
founder, co-founder, ceo, cto, coo, chief technology, chief executive,
vp engineering, vp of engineering, head of engineering,
director of engineering, engineering manager, eng lead, technical lead,
recruit, talent, people ops, head of people, hiring
```

`is_founder` is set when `looks_like_person_name(founder_name)` and the contact's
`first_name`+`last_name` match the LLM-extracted `founder_name`
case-insensitively, **or** `position` contains "founder". The name match is the
reason `looks_like_person_name` survives.

### Research flow after the change

```
resolve_company_url          (was resolve_lead_url)
  → scrape_website
  → call_llm_extraction → parse_llm_response
  → commit_research(company_id, ...)
  → find_contacts(domain)                    # ONE Hunter domain-search call
  → classify_contacts(contacts, founder_name)
  → save_contacts(company_id, contacts)      # bulk upsert, ON CONFLICT DO NOTHING
  → if not has_eligible_contact: terminal failure, research_status='failed'
  → else research_status='researched'
```

The terminal-failure reason string changes from
`"No founder email found (Hunter)"` to `"No eligible contacts found (Hunter)"`.

Contacts are saved **before** the eligibility gate so ineligible contacts are
still recorded. A later loosening of `DECISION_MAKER_PATTERNS` can then re-run
classification over stored rows instead of re-spending Hunter credits.

## Renames

### `errors.py` — the failure choke point

`handle_terminal_failure(lead_id, reason, *, stage, task_name)` becomes two
functions, because the two levels update different tables:

```python
def fail_company(company_id, reason, *, stage, task_name) -> None
def fail_outreach(outreach_id, reason, *, stage, task_name) -> None
```

Keeping one function with a nullable `company_id`/`outreach_id` pair would push
the branch into every caller and make the `CHECK` constraint reachable by
accident. Two named functions make the level explicit at each call site.
`handle_transient_failure` takes either id and only logs, so it stays as one
function.

### Module and symbol renames

| Old | New |
|---|---|
| `api/routes/leads.py` | `api/routes/outreach.py` |
| `research/helpers/email_finder.py` | `research/helpers/contact_finder.py` |
| `preflight.resolve_lead_url` | `preflight.resolve_company_url` |
| `LeadResolution.lead` | `CompanyResolution.company` |
| `shared/db_helpers.update_lead_status` | `update_company_research_status`, `update_outreach_status` |
| `shared/db_helpers.record_dead_letter` | signature takes `company_id=` or `outreach_id=` |
| `database.Lead` | `database.Company`, `database.CompanyContact`, `database.Outreach` |
| `views.PendingDraft.lead_id` | `.outreach_id` (+ `user_id`, `contact_*` fields) |
| `views.PendingSend.lead_id/founder_email` | `.outreach_id` / `.contact_email` |
| `research/db_helpers.save_founder_contact` | `save_contacts` (bulk) |
| `drafting/db_helpers.fetch_pending_drafts` | unchanged name, reshaped row |
| `discovery` dedup on `Lead.company_name` | on `Company.company_name` |

`system.py`'s health check counts `Lead`; it becomes `Company`.

### The greeting and recipient title

`prompts/email_draft.py` hardcodes `RECIPIENT_TITLE = "Founder"` with the comment
"startups.gallery leads are founders; no title column upstream." Both halves of
that stop being true here: there *is* a title column now
(`company_contacts.position`), and the recipient is frequently not a founder.

- `RECIPIENT_TITLE` is deleted. `build_email_draft_messages` takes
  `recipient_position` and interpolates the contact's real position, falling back
  to `"Founder"` only when `position` is NULL.
- `assemble_email` derives the greeting's `first_name` from
  `contact_first_name`, **not** `company.founder_name`. Addressing the CTO by the
  founder's first name is the single most obvious way this feature could embarrass
  a user, and it is a one-line mistake to make.
- The template itself is unchanged. Its `{{first_name}}` token simply receives a
  different value, and the eligibility filter keeps the founder-flavored body
  honest by restricting recipients to decision-makers and hiring roles.

### The drafting bridge

After the split, nothing creates `outreach` rows until Stack 3 adds the pool UI —
so the pipeline would silently stop drafting. To keep every stack shippable,
`drafting_task` gains a temporary bridge: before sweeping, it inserts `queued`
outreach rows for the admin user for every `researched` company that has an
eligible contact and no existing outreach row for that user.

This exactly preserves today's behaviour (admin drafts everything researched) and
is **deleted in Stack 3**, where user selection replaces it. It is marked in code
with a comment naming Stack 3 as its removal point, so it does not become
permanent by accident.

Contact selection in the bridge is simply "highest-confidence eligible contact" —
the real least-used-with-cap selection is Stack 3's.

## Frontend

`components/ReviewDeck.tsx` and `LeadExplorer.tsx` (extracted in 1a) are updated:

- `LeadExplorer` → `CompanyExplorer`; a row is now a company plus *this user's*
  outreach state (or none).
- The review deck shows the **contact** being emailed — name and position — not
  just the company's founder. This is the user-visible payoff of contact
  spreading, and without it a user cannot tell who they are about to email.
- `lib/api.ts`: `/leads/*` → `/outreach/*`; `lead_id` → `outreach_id`.
- Status badges gain `queued`.

## Testing

`tests/test_migration.py` — new, and the highest-value test in this stack. Seeds
a fixture resembling production, runs the migration, asserts the result:

- Leads in every status → correct `research_status` / `outreach.status` pairs.
- A lead with `founder_email` yields exactly one contact,
  `is_founder=true, eligible=true, confidence=25`.
- A lead without one yields no contact and no outreach row.
- `research` rows still resolve to the right company after the rename.
- `drafts.outreach_id` points at the outreach row for the same company.
- `dead_letter` rows land on `company_id` or `outreach_id` per stage, and the
  `CHECK` constraint holds for every row.
- `leads_legacy` still contains every original row.
- Migration with no admin user → aborts.

`tests/test_contact_finder.py`:

- `classify_contacts` marks generic-type contacts ineligible.
- Sub-threshold confidence → ineligible.
- A non-decision-maker position (e.g. "Staff Accountant") → ineligible.
- Every `DECISION_MAKER_PATTERNS` entry → eligible.
- `is_founder` set by name match and, separately, by position containing
  "founder".
- `has_eligible_contact` false for an all-generic result → research fails fast.
- `find_contacts` maps Hunter's payload shape correctly; network error → `[]`
  (non-fatal, matching the current `find_email` contract).
- `domain_from_url` regression tests carried over unchanged.

`tests/test_research.py`, `test_drafting.py`, `test_logistics.py`,
`test_discovery.py`, `test_api.py` — follow the rename. `test_api.py` gains
tenancy-isolation tests: user A cannot read or mutate user B's outreach.

## Documentation updated in this stack

- `CLAUDE.md` — the schema section, all four worker pipeline descriptions, the
  endpoint table, the DLQ section, and the Hunter description. The
  "Email-finding (Hunter.io)" bullet is rewritten for Domain Search and contact
  spreading.
- `docs/architecture-flow.md` — rewritten for the global-pool / per-user-outreach
  model.
- `README.md` — the pipeline overview and status vocabulary.
- **Diagrams → Mermaid.** `coreArchitecture.svg`, `pipeline.svg`, and
  `lifcecycle.svg` are replaced by Mermaid blocks in
  `docs/architecture-flow.md`; the SVG files are deleted. The `lifcecycle` →
  `lifecycle` typo dies with them.
- `docs/superpowers/specs/2026-04-18-cold-email-agent-design.md` and
  `plans/2026-04-18-boilerplate.md` — **not rewritten.** A single note at the top
  marks them as describing the superseded single-tenant model and links here.

## Out of scope for 1b

Pool browsing UI, least-used contact selection, the per-contact cap `K`, quotas,
the Redis token bucket, per-user résumés and Gmail credentials, and scheduling.
The drafting bridge stands in for user selection until Stack 3.
