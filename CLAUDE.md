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
| **Company Pool** | `GET /api/companies` | Read-only global company pool, filtered (industry, funding stage, headcount, search, founder-reachable) and with companies the CALLER already targeted excluded — never returns an address, only `contact_count` / `has_founder_contact` |
| **Company Detail** | `GET /api/companies/{id}` | One company with full research and contact SUMMARIES (`first_name`, `position`, `is_founder`) — still no addresses |
| **Create Outreach** | `POST /api/outreach` | Queues drafts for the caller's selected `company_ids`; partial success (`created` / `skipped`, see Contact Spreading and Rate Limiting & Quota below), dispatches `drafting_task(user_id)` once for the batch |
| **Review Queue** | [`/api/outreach/drafts`](https://cold-email-backend-426138953095.us-central1.run.app/api/outreach/drafts) | THIS user's drafted outreach rows pending human review |
| **List Outreach** | `GET /api/outreach` | THIS user's outreach rows, paginated/filtered/searched |
| **Trigger Discovery** | `POST /api/pipeline/discovery` | Enqueues a new startup discovery sweep (admin) |
| **Trigger Drafting** | `POST /api/pipeline/drafting` | Manually runs the hourly `drafting_recovery_task` safety net for users with stale `queued` rows (admin) — drafting itself is dispatched per-user by `POST /api/outreach`, not this route |
| **Requeue Research** | `POST /api/pipeline/research` | Re-dispatches research for companies stuck in `found` (recovers orphaned companies; admin) |
| **Approve Outreach** | `POST /api/outreach/{id}/approve` | Approves THIS user's draft and dispatches `logistics_task` (Gmail send); 404 if the row isn't the caller's |
| **Reject Outreach** | `POST /api/outreach/{id}/reject` | Marks THIS user's outreach rejected with optional notes; 404 if the row isn't the caller's |
| **Regenerate Draft** | `POST /api/outreach/{id}/regenerate` | Resets THIS user's outreach to `queued` and triggers re-drafting; 404 if the row isn't the caller's |
| **List DLQ** | `GET /api/dlq` | Lists dead-lettered (terminally-failed) tasks awaiting retry |
| **Retry DLQ** | `POST /api/dlq/retry` | Re-dispatches dead-lettered tasks (optional `?stage=research\|drafting\|logistics`); resets each row and clears it (admin) |
| **Google Login** | `GET /api/auth/google/login` | Returns `{authorize_url}` for the consent screen (carries a signed CSRF state nonce) |
| **OAuth Callback** | `GET /api/auth/google/callback` | Verifies state, exchanges the code, sets the session cookie, redirects to `FRONTEND_URL` |
| **Current User** | `GET /api/auth/me` | `{id, email, name, picture_url, role, gmail_connected, profile_complete}` — the only source of client-side auth state; `profile_complete` drives the frontend's onboarding gate (see Sender Identity below) |
| **Logout** | `POST /api/auth/logout` | Clears the session cookie |
| **Get Profile** | `GET /api/profile` | The caller's sender profile (name, intro, links, `experience_pool`, `company_links`, `has_resume`, `resume_filename`); 404 if they haven't created one yet |
| **Save Profile** | `PUT /api/profile` | Creates or replaces the caller's profile fields — never the résumé bytes. 422s if any `experience_pool` bullet lacks the `"Label: achievement"` separator |
| **Upload Résumé** | `POST /api/profile/resume` | Multipart PDF upload. Validates → stores the bytes → returns a **suggested** profile parsed by the LLM; nothing besides the résumé itself is saved until a follow-up `PUT /api/profile` |
| **Download Résumé** | `GET /api/profile/resume` | Streams the caller's own stored PDF back; 404 if none stored |
| **Delete Résumé** | `DELETE /api/profile/resume` | Clears the stored PDF, keeping the rest of the profile intact |
| **Get Quota** | `GET /api/quota` | The caller's monthly draft quota: `{used, limit, period_end}` (see Rate Limiting & Quota below) |
| **Get LLM Key** | `GET /api/llm-key` | Whether a BYOK key is configured, its provider, and its last 4 characters — never the key itself |
| **Set LLM Key** | `PUT /api/llm-key` | Stores a BYOK key after validating it with one live call; 422 if validation fails, so a bad key fails a form instead of 40 drafts one at a time |
| **Delete LLM Key** | `DELETE /api/llm-key` | Removes the stored BYOK key, reverting the caller to the platform's shared bucket and quota |

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

## 👤 Sender Identity (per-user)

There is no more compiled-in identity. `sender_profile.PROFILE` and the
committed `cold_email/resume.txt` / `resume.pdf` are gone; every user gets
their own row in **`profiles`** (`user_id` PRIMARY KEY — one profile per
user) holding `name`, `intro`, `linkedin`, `github`, `website`,
`experience_pool` (JSONB list of `"Company: achievement"` bullets),
`company_links` (JSONB dict, label → URL, for the bolded/linked name in the
email), plus the résumé itself: `resume_pdf` (BYTEA), `resume_filename`,
`resume_text`, `parsed_at`. `SenderProfile.from_row` builds the in-memory
dataclass a worker actually uses from that row.

- **Upload → parse → confirm, never upload → save.** `POST /api/profile/resume`
  validates the bytes (`resume_store.validate_resume` — magic-byte check,
  5MB cap; see below), stores them, extracts text with `pypdf`, then asks the
  LLM for a **suggested** profile (`profile_extract.suggest_profile`). That
  suggestion is returned to the caller and saved NOWHERE — the frontend shows
  it in an editable form, and only a follow-up `PUT /api/profile` persists
  anything the user confirmed. This is why a scanned/image PDF (extraction
  fails, 422) or a bad LLM parse never corrupts a profile: the résumé bytes
  are already safely stored by the time either can fail, and the user can
  always fall back to filling the form in by hand.
- **`resume_store.py` (`cold_email/resume_store.py`) is the entire read/write
  surface for résumé bytes** — no other module touches `profiles.resume_pdf`
  directly. Stored as `bytea` on the `profiles` row rather than in GCS: at
  ~400KB/user the storage cost difference is negligible, and `bytea` wins
  because **the profile row and the PDF commit in ONE transaction**. With GCS
  the row and the blob are two separate systems, and a crash between the blob
  write and the row commit leaves an orphan file in the bucket that nothing
  ever reaps — reconciling that is a job someone owns forever. A single
  Postgres transaction can't fail half-committed.
  - Because `bytea` defaults to Postgres's `EXTENDED` TOAST strategy (compress
    then out-of-line), and PDFs are already compressed, every write burns CPU
    compressing bytes that don't get smaller. `resume_pdf` is set to `STORAGE
    EXTERNAL` (out-of-line, uncompressed) instead — see `migrations/storage.sql`
    and `scripts/apply_storage.py` in the deployment section below for how that
    setting actually reaches a database `Base.metadata.create_all` provisioned.
  - **The 5MB cap (`resume_store.MAX_RESUME_BYTES`) is not just an upload
    nicety.** Cloud SQL disk grows automatically but **never shrinks** — an
    unbounded upload path would permanently inflate the instance (and every
    backup taken of it) the first time someone uploads an oversized PDF.
- **Gmail credentials split app-level from user-level — do not conflate
  them.** `gmail_client_id` / `gmail_client_secret` (`cold_email/config.py`,
  set via env) identify the OAuth *application* to Google and stay app-level;
  Google requires them to refresh **any** user's token, so there's exactly one
  pair for the whole deployment. `refresh_token` and `sender_email` are
  per-user: encrypted (Fernet) on the `users` row, resolved per-sweep by
  `resolve_gmail_credentials` into a `GmailCredentials(refresh_token,
  sender_email)` that's passed as an argument everywhere a Gmail call is made
  (`workers/shared/gmail_client.py`), never read from settings. Moving all
  four values onto the `users` table is the classic multi-tenant OAuth
  mistake — nothing could then be refreshed. `resolve_gmail_credentials`
  returns `None` (not an error) when Google omitted a refresh token for a
  user who'd already consented before — the profile page's "Reconnect Gmail"
  button re-runs the same consent flow (`prompt=consent` forces Google to
  issue one again).
- **Onboarding flow (frontend):** sign in with Google → land on `/onboarding`
  if `profile_complete` is false → upload a résumé (optional — "Skip and fill
  in manually" exists for a scanned PDF) → review/edit the extracted
  suggestion → save, which is the first `PUT /api/profile`.

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

3. **Drafting (`cold_email.workers.drafting.drafting_task`)**:
   - **On-demand per user, not a Beat sweep.** Dispatched by `POST /api/outreach` (`drafting_task.delay(str(user.id))`) the moment that user's selected companies are queued. One dispatch drafts every `outreach` row currently `status = 'queued'` for that ONE user (via the `pending_drafts` view, joined to company, contact, and research) — a batch per user, not per company, since the task already sweeps everything that user just queued.
   - **Per-user isolation is structural, not a convention to remember.** An earlier shape of this task took no arguments and grouped every tenant's queued rows itself in one pass — which meant a single dispatch could draft rows belonging to several users using the FIRST owner's profile, résumé, and Gmail mailbox. `drafting_task(user_id)` cannot reach another user's rows by construction; the cross-tenant grouping now happens one level up, in `drafting_recovery_task`, for the same reason it always existed — one dispatch must never load two users' credentials into one draft.
   - **`drafting_recovery_task` is the hourly safety net, not the primary path** (Celery Beat, see the schedule below). It finds users with an `outreach` row stuck `queued` for more than `RECOVERY_STALE_MINUTES` (30) — evidence the original dispatch never happened, e.g. a Redis hiccup inside `POST /api/outreach` — and re-dispatches `drafting_task` for each of them.
   - **Loads the sending user's identity ONCE per dispatch, not per row.** `load_sender_context` reads the `profiles` row, résumé bytes, and Gmail credentials a single time before the per-row loop — a résumé's bytes cross the DB connection on every read, so doing this per row would pull the same ~400KB file out of Cloud SQL once per queued outreach row. Two outcomes are preflight failures for the WHOLE batch, not any one row, because neither is fixable by trying a different row: no `profiles` row (or one missing `name`/`intro`) → aborts as `"no_profile"`; Gmail disconnected (`resolve_gmail_credentials` returns `None`) → aborts as `"gmail_disconnected"`. Either way it returns early, **every queued row is left exactly at `status = 'queued'` and no dead-letter row is written** — completing the profile or reconnecting Gmail lets the next dispatch (a manual regenerate, or the recovery sweep) pick everything up with no manual DLQ retry. (A *missing résumé* is not one of these: it's not terminal, since the email body still renders from `intro` + `experience_pool` without an attachment.)
   - No repo-relative `resume.pdf` is read anymore — the attachment (if any) comes back from `resume_store.get_resume_sync` as `(filename, bytes)` alongside the rest of that dispatch's `SenderContext`.
   - **Template-driven, not freeform.** A fixed candidate-outreach template (`prompts/email_template.py`) owns structure/tone; the LLM (via `generate_json` with the `EmailDraftContext` schema) fills only the *contextual slots* — subject, a company-interest phrase, an admiration detail, and the 3 most-relevant experience bullets tailored per company from that user's `SenderProfile.experience_pool`. `assemble_email` fills the template (`fill_template` raises on any unfilled `{{token}}`) and renders HTML + a plain-text fallback (`helpers/html_builder.py`). Per-row (not batch-wide) failures — a missing contact email or empty model output — go through `fail_outreach` (`ERR_NO_CONTACT_EMAIL` / `ERR_EMPTY_DRAFT`), terminal for that one row only, so one bad row never aborts the rest of the batch. See **Rate Limiting & Quota** below for how the LLM call itself is throttled and metered across every user drafting at once.
   - Creates a **multipart** Gmail draft via `create_draft(creds, to, subject, body, html=..., attachment=(filename, bytes))` (plain fallback + rich HTML with bold, a bullet list, clickable GitHub/LinkedIn links, and the résumé PDF attached) using that user's `GmailCredentials`, and saves `gmail_draft_id`.
   - Advances the outreach row to `status = 'drafted'` (held in review queue).

4. **Logistics (`cold_email.workers.logistics.logistics_task`)**:
   - Event-driven per outreach row: triggered when a human clicks **Approve** in the frontend (`POST /api/outreach/{id}/approve`).
   - Sends the stored Gmail draft via Gmail API (`send_draft`).
   - Advances the outreach row to `status = 'sent'`.

---

## 🎯 Contact Spreading (`cold_email.contact_selection.select_contact`)

The company pool is fully shared, so without spreading, every user who selects
the same company lands on the same eligible contact, and that one person
receives N near-identical emails from N different senders — reads as a spam
farm, not personalized outreach. `select_contact(session, company_id, cap)`,
called from `POST /api/outreach`, picks the least-globally-contacted eligible
contact instead, reading the `available_contacts` view (`use_count` computed
across **every** user, not just the caller):

```sql
ORDER BY use_count ASC, confidence DESC, is_founder DESC, contact_id ASC
LIMIT 1
```

- **`use_count ASC` dominates the ordering** — spreading is the entire reason
  this function exists, so it sorts first, ahead of everything else.
- **`confidence DESC`** — among contacts tied on `use_count`, prefer the one
  Hunter is more sure is actually deliverable.
- **`is_founder DESC` sits BELOW `use_count`, deliberately.** Ranking founders
  first regardless of use would re-concentrate volume on exactly the address
  spreading exists to protect: the founder would win every tie and get
  selected first repeatedly, defeating the point of the ordering.
- **`contact_id ASC`** — a total ordering so two contacts tied on every other
  column still return deterministically. Without it, Postgres may return
  either of two equal rows on different calls, which looks like a selection
  bug in a test re-run when it's really just a missing tiebreaker.
- `select_contact` returns `None` once every eligible contact at a company
  has hit `cap` (`settings.contact_cap`, default 3, overridable via
  `CONTACT_CAP`) — `POST /api/outreach` turns that into a `no_available_contact`
  skip, and the company drops out of the pool entirely (`companies.py`'s
  `avail.contact_count > 0` filter).

**The cap is a heuristic, not an invariant.** Reading `use_count` and creating
the `outreach` row are two separate steps, so two concurrent `POST
/api/outreach` requests can both read a contact at `use_count = cap - 1`, both
pass the check, and both insert — landing on `cap + 1`. This is accepted
rather than fixed: enforcing the cap exactly would mean serializing pool
selection (e.g. `SELECT ... FOR UPDATE`) across every user hitting that
endpoint at once, to protect a bound that is itself a judgment call, not a
correctness requirement. Occasionally routing a fourth user to a
contact instead of three is a cheaper failure mode than that lock contention.

---

## 🚦 Rate Limiting & Quota

Two independent limits sit between a user selecting companies and an LLM call
actually happening — one protects the shared provider budget, the other
protects the platform's cost per user.

**Fleet-wide Redis token bucket (`cold_email.workers.shared.rate_limit`)**
replaces what used to be a `time.sleep(LLM_MIN_INTERVAL_SECONDS)` between
calls inside a single drafting sweep.

- **Why a sleep was insufficient.** A `sleep` paces ONE worker process. The
  real constraint — a provider's free-tier RPM/RPD — is shared by every
  worker, every user, and every task type calling that model. With exactly
  one user drafting, "pace this process" and "respect the provider's quota"
  were indistinguishable, so the sleep happened to work. With N users
  drafting concurrently — each a separate `drafting_task(user_id)` dispatch,
  each its own process — sleep **guarantees** 429s: every process politely
  waits its own fixed interval with no idea any other process exists, so
  nothing stops several of them from calling the same model in the same
  second.
- **`acquire(key, rate, burst, timeout)`** takes one token from a
  Redis-backed bucket keyed per model (`llm:{model}`) before every
  `generate_json` call. The check-and-decrement runs as a Lua script so it's
  ATOMIC — a read-then-write in Python has a race between processes that
  would defeat the whole point.
- **Fails OPEN, not closed**, if Redis is unreachable: failing closed would
  turn a Redis blip into a total drafting outage, whereas failing open only
  risks the 429s the model fallback chain already tolerates.
- **BYOK bypasses the bucket entirely** — it exists to protect the
  platform's shared quota, and a BYOK call isn't spending it.

**Per-user monthly draft quota (`cold_email.quota`)** —
`users.monthly_draft_quota` (default 100) caps how many `outreach` rows a
user may **create** per UTC calendar month, not how many they send. The LLM
call — the actual cost — happens at drafting, so a user who drafts 100 and
approves 3 has already spent 100 units of the thing being rationed.

- `quota.check(session, user, requested)` **clamps rather than rejects**:
  `POST /api/outreach` creates as many of the requested companies as the
  remaining quota allows and reports the rest as `skipped:
  quota_exceeded` — a user selecting 20 with 12 left gets 12 drafts and a
  clear note, not a 400 and nothing (see the partial-success semantics in
  `outreach.py`).
- Calendar month in UTC, not a rolling 30-day window, so remaining quota
  doesn't drift unpredictably day to day.
- **BYOK bypasses the quota too**, for the same reason it bypasses the
  bucket: it's the user's own provider limits being spent, not the
  platform's.

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
- **`users`**: One row per authenticated person. `role` (`user`/`admin`), Fernet-encrypted `gmail_refresh_token_enc`, `gmail_sender_email`. `monthly_draft_quota` (Integer, default 100) — the per-user monthly cap `quota.check`/`quota.usage` enforce (see Rate Limiting & Quota above). Optional BYOK: `llm_api_key_enc` (Fernet ciphertext) and `llm_provider` (`groq`|`gemini`), set together via `PUT /api/llm-key` and resolved per-call by `resolve_llm_credentials`. A configured BYOK key bypasses BOTH the shared Redis token bucket and `monthly_draft_quota` — a BYOK call spends the user's own provider limits, not the platform's.
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

# Gmail OAuth APPLICATION credentials — app-level, not per-user. Google
# requires them to refresh ANY user's token, so there is exactly one pair for
# the whole deployment. Per-user refresh tokens live encrypted on the `users`
# table (gmail_refresh_token_enc), never here — see Sender Identity above.
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...

# Sender identity (name, intro, links, résumé, experience bullets) is
# per-user data in the `profiles` table, not code — see Sender Identity above.

# Max users who may ever email a single contact. A SPREADING HEURISTIC, not
# an invariant — concurrent requests can exceed it by one (see Contact
# Spreading above); not worth serializing pool selection to enforce exactly.
CONTACT_CAP=3

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
express, same class of gap as the views — **and**, since this stack, applies
`migrations/008_user_llm_and_quota.sql`'s `ADD COLUMN IF NOT EXISTS` on
`users` every boot too). Both run on **every boot**, not
just the first, because the underlying DDL is idempotent (re-declaring a view,
re-applying the same `STORAGE` strategy, or re-running `ADD COLUMN IF NOT
EXISTS` is a no-op).

**The general rule:** `create_all` only ever issues `CREATE TABLE` — it never
alters a table that already exists. A migration that only creates NEW tables
needs nothing further (create_all already covers it, same as 006's new
`companies`/`company_contacts`/`outreach` tables). **Any migration that
`ALTER`s an EXISTING table must be appended to `scripts/apply_storage.py`'s
`SQL_FILES` tuple** — like 008's three `ADD COLUMN IF NOT EXISTS` statements
on `users` — or it is silently invisible on every deploy after the first,
because nothing in the boot sequence otherwise runs it. Don't invent a second
script-plus-boot-hook for this class of gap.

Migration 006 predates this rule and is the one exception still requiring a
manual, one-time apply (see below) — its `ALTER TABLE`s aren't idempotent
`ADD COLUMN IF NOT EXISTS` shapes, and it also renames/backfills data, which
`apply_storage.py`'s "safe on every boot" contract does not cover. Skipping
006 is
the DEFAULT outcome of a normal deploy, not an edge case, and it "succeeds"
quietly:
- `create_all` makes the new tables, empty. The old `leads` table and its data
  are untouched and never migrated.
- The stale pre-migration views (if this is the first deploy off `leads`) or
  the newly-declared post-migration views (if `views.sql` ran against an
  un-migrated schema) survive either way.
- `GET /api/health` only checks DB connectivity, so it still returns 200 —
  Cloud Run's health check passes and cuts traffic over to the new revision.
- `drafting_task` then raises `TypeError: PendingDraft() got an unexpected
  keyword argument 'lead_id'` (or the equivalent "relation does not exist" /
  missing-column error) the moment a user's `POST /api/outreach` dispatches
  it, and again every hour when `drafting_recovery_task` retries the same
  stuck rows — every lead/company sits stranded, nothing is ever drafted or
  sent, with no user-visible error anywhere in the dashboard.

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
