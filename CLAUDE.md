# Cold Email Agent — System & Architecture Guide

Autonomous cold email outreach system: discovers early-stage startups from directory listing pages via Firecrawl, researches each company and founder using DuckDuckGo (keyless web search), web scraping, and a provider-agnostic LLM layer (Groq + Google Gemini with automatic failover), generates personalized email drafts pausing in a human review queue, and sends approved drafts via the Gmail API.

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
| **Frontend Dashboard** | [https://cold-email-puce-nine.vercel.app](https://cold-email-puce-nine.vercel.app) | Live Next.js review deck, approvals & lead explorer |
| **Backend REST API** | [https://cold-email-backend-426138953095.us-central1.run.app](https://cold-email-backend-426138953095.us-central1.run.app) | Cloud Run FastAPI backend |
| **Health Check** | [`/api/health`](https://cold-email-backend-426138953095.us-central1.run.app/api/health) | API & Cloud SQL database connectivity check |
| **Pipeline Stats** | [`/api/pipeline/stats`](https://cold-email-backend-426138953095.us-central1.run.app/api/pipeline/stats) | Lead counts by status (`found`, `researched`, `drafted`, `approved`, `sent`, `rejected`, `failed`) |
| **Review Queue** | [`/api/leads/drafts`](https://cold-email-backend-426138953095.us-central1.run.app/api/leads/drafts) | Drafted leads pending human review |
| **Trigger Discovery** | `POST /api/pipeline/discovery` | Enqueues a new startup discovery sweep |
| **Trigger Drafting** | `POST /api/pipeline/drafting` | Enqueues a batch sweep drafting emails for researched leads |
| **Requeue Research** | `POST /api/pipeline/research` | Re-dispatches research for leads stuck in `found`/`failed` (recovers orphaned leads) |
| **Approve Lead** | `POST /api/leads/{id}/approve` | Approves draft and dispatches `logistics_task` (Gmail send) |
| **Reject Lead** | `POST /api/leads/{id}/reject` | Marks lead as rejected with optional notes |
| **Regenerate Draft** | `POST /api/leads/{id}/regenerate` | Resets lead to `researched` and triggers re-drafting |
| **List DLQ** | `GET /api/dlq` | Lists dead-lettered (terminally-failed) tasks awaiting retry |
| **Retry DLQ** | `POST /api/dlq/retry` | Re-dispatches dead-lettered tasks (optional `?stage=research\|drafting\|logistics`); resets each lead and clears its row |

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
   - Deduplicates and saves new leads into `leads` table (`status = 'found'`).
   - Automatically enqueues `research_task.delay(lead_id)` for each new lead.
   - Also scheduled via Celery Beat every Monday at 8:00 AM.

2. **Research (`cold_email.workers.research.research_task`)**:
   - Resolves the official company website via DuckDuckGo (`ddgs`, keyless) — candidates scored by `select_best_url` (aggregator blocklist + slug match). Non-fatal search errors propagate so the task retries instead of terminally failing the lead.
   - Scrapes `/about`, `/team`, and homepage content using BeautifulSoup, falling back to Firecrawl.
   - Calls the LLM via `generate_json` (see provider-agnostic layer below) to extract founder name, tech stack, recent news, and an interest hook.
   - **Email-finding (Hunter.io):** resolves the founder's work email from name + company domain via `find_email` (`helpers/email_finder.py`). The directory sources never carry an address, so this is where a lead becomes emailable.
   - **Fail-fast gate:** `should_accept_email` accepts only a real address whose Hunter confidence ≥ `MIN_EMAIL_SCORE` (50). No usable email → the lead is **dead-lettered at research** (`handle_terminal_failure`, stage `research`) and never advances, so it doesn't waste the drafting stage. Otherwise the email is saved and `lead.status = 'researched'`.
   - Recoverable: `POST /api/pipeline/research` re-dispatches research for leads stuck in `found`/`failed` (discovery only enqueues research for brand-new leads).

3. **Drafting Sweep (`cold_email.workers.drafting.drafting_task`)**:
   - Batch sweep: queries the `pending_drafts` database view for all leads that reached `status = 'researched'`.
   - Generates a personalized subject line and body via `generate_json`, pacing calls under the LLM free-tier limits.
   - Creates a Gmail draft in your mailbox via Gmail API (`create_draft`) and saves `gmail_draft_id`.
   - Advances lead to `status = 'drafted'` (held in review queue).
   - Scheduled via Celery Beat to sweep every 15 minutes.

4. **Logistics (`cold_email.workers.logistics.logistics_task`)**:
   - Event-driven per lead: triggered when human clicks **Approve** in the frontend (`POST /api/leads/{id}/approve`).
   - Sends the stored Gmail draft via Gmail API (`send_draft`).
   - Advances lead to `status = 'sent'`.

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

Every terminal failure funnels through one choke point — `handle_terminal_failure(lead_id, reason, *, stage, task_name)` (`workers/shared/errors.py`) — which both marks the lead `failed` **and** writes a `dead_letter` row. So a permanently-failed lead is visible on the lead *and* independently retryable.

- **Producers:** research (no usable email), drafting (no founder email backstop, empty model output).
- **Retry:** `POST /api/dlq/retry` resets each lead to its stage's input state (`research`→`found`, `drafting`→`researched`, `logistics`→`approved`), re-dispatches the worker, and deletes the row. A task that fails again is re-written to the DLQ by the same choke point, so the queue self-cleans.
- **Inspect:** `GET /api/dlq` lists rows with the joined company name, stage, error, and retry count.
- On first boot after migration `004`, `start.sh` provisions the table via idempotent `Base.metadata.create_all`.

---

## 🗄️ Database Schema & Views

- **`leads`**: Company info, founder info, status (`found`, `researched`, `drafted`, `approved`, `sent`, `rejected`, `failed`), error logs.
- **`research`**: Tech stack JSONB, recent news, value proposition hook, raw scraped content.
- **`drafts`**: Subject line, generated email body, `gmail_draft_id`, version, reviewer notes.
- **`dead_letter`**: Terminally-failed tasks — `lead_id`, `task_name`, `stage`, `error_msg`, `retry_count` (see DLQ above).
- **`pending_drafts` View**: Distinct leads in `researched` status joined with their latest research.
- **`pending_sends` View**: Leads in `approved` status joined with their latest `gmail_draft_id`.

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
HUNTER_API_KEY=...                   # Hunter.io Email Finder (founder email discovery in research)
# URL discovery uses DuckDuckGo (ddgs) — keyless, no API key needed.
# Optional: MODEL_FALLBACK_CHAIN=["llama-3.3-70b-versatile","gemini-3.5-flash-lite"]
# Optional: MODEL_NAME=gemini-flash-latest   (default single model when chain unset)

# Gmail API — OAuth2 refresh-token flow (headless send from a single mailbox).
# Mint these once with: uv run python scripts/gmail_auth.py --client-secret <client.json>
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
GMAIL_REFRESH_TOKEN=...
GMAIL_SENDER_EMAIL=...

# Sender Identity
SENDER_NAME="Liyu Xiao"
SENDER_ROLE="Software Engineer, Ledger Team"
SENDER_COMPANY="Wealthsimple"

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
