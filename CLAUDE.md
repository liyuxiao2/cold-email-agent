# Cold Email Agent — System & Architecture Guide

Autonomous cold email outreach system: discovers early-stage startups from directory listing pages via Firecrawl, researches each company and founder using Brave Search, web scraping, and Google Gemini LLM, generates personalized email drafts pausing in a human review queue, and sends approved drafts via the Gmail API.

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
| **Approve Lead** | `POST /api/leads/{id}/approve` | Approves draft and dispatches `logistics_task` (Gmail send) |
| **Reject Lead** | `POST /api/leads/{id}/reject` | Marks lead as rejected with optional notes |
| **Regenerate Draft** | `POST /api/leads/{id}/regenerate` | Resets lead to `researched` and triggers re-drafting |

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
   - Resolves official company website using Brave Search API.
   - Scrapes `/about`, `/team`, and homepage content using Firecrawl / BeautifulSoup.
   - Calls Google Gemini LLM (`gemini-2.5-flash`) to extract tech stack, recent news, value hook, and founder name.
   - Updates `research` table and marks `lead.status = 'researched'`.

3. **Drafting Sweep (`cold_email.workers.drafting.drafting_task`)**:
   - Batch sweep: queries the `pending_drafts` database view for all leads that reached `status = 'researched'`.
   - Generates personalized subject lines and email body using Gemini.
   - Creates a Gmail draft in your mailbox via Gmail API (`create_draft`) and saves `gmail_draft_id`.
   - Advances lead to `status = 'drafted'` (held in review queue).
   - Scheduled via Celery Beat to sweep every 15 minutes.

4. **Logistics (`cold_email.workers.logistics.logistics_task`)**:
   - Event-driven per lead: triggered when human clicks **Approve** on the dashboard.
   - Sends the stored Gmail draft via Gmail API (`send_draft`).
   - Advances lead to `status = 'sent'`.

---

## 🗄️ Database Schema & Views

- **`leads`**: Company info, founder info, status (`found`, `researched`, `drafted`, `approved`, `sent`, `rejected`, `failed`), error logs.
- **`research`**: Tech stack JSONB, recent news, value proposition hook, raw scraped content.
- **`drafts`**: Subject line, generated email body, `gmail_draft_id`, version, reviewer notes.
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
FIRECRAWL_API_KEY=fc-...
GEMINI_API_KEY=AQ...
BRAVE_API_KEY=...

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
