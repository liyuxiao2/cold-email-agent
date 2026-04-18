# Cold Email Agent — Design Spec
_Date: 2026-04-18_

## Context

The goal is to build an autonomous cold email pipeline that finds early-stage fintech/high-growth startup companies, researches each one deeply, drafts a personalized email anchored in specific technical context (e.g., ledger systems, payment infrastructure), holds for human review, and then schedules delivery. The system is built entirely from scratch in Python so the builder understands every layer — no all-in-one tools like Clay. The emphasis is on learning: async Python, Celery distributed task queues, LLM integration, and external API orchestration.

---

## Architecture

**Stack:**
- **Language:** Python 3.12+
- **Task Queue:** Celery + Redis (broker + result backend)
- **Database:** Postgres (via SQLAlchemy async + asyncpg)
- **Scheduler:** Celery Beat (cron-style triggers)
- **Web Server:** FastAPI (HITL dashboard)
- **Infrastructure:** Docker Compose (Redis + Postgres locally)
- **Package Management:** `uv` + `pyproject.toml`

**External APIs:**
- Apollo.io — lead sourcing (company + contact search)
- Firecrawl — web scraping (JS-rendered pages → markdown)
- Anthropic Claude (claude-sonnet-4-6) — research extraction + email drafting
- Instantly.io — email sequence scheduling + domain reputation management

**Data flow:**

```
Celery Beat (Monday 8am)
    │
    ▼
discovery_task ──▶ Apollo.io
    │  (inserts leads, status=found)
    │
    └──chains──▶ research_task ──▶ Firecrawl + Claude (extraction)
                    │  (status=researched)
                    │
                    └──chains──▶ drafting_task ──▶ Claude (email generation)
                                    │  (status=drafted)
                                    │
                              [HITL PAUSE — FastAPI dashboard]
                                    │
                              User approves
                                    │
                                    ▼
                              logistics_task ──▶ Instantly.io
                                    (status=sent)
```

Workers never talk to each other directly. All state is written to Postgres; all task dispatch goes through Redis.

---

## Project Structure

```
cold-email-agent/
├── docker-compose.yml           # Redis + Postgres
├── pyproject.toml               # uv-managed dependencies
├── .env.example                 # API keys template
├── .gitignore
│
├── cold_email/
│   ├── __init__.py
│   ├── config.py                # pydantic-settings — loads .env
│   ├── database.py              # SQLAlchemy models + async engine
│   ├── celery_app.py            # Celery app + Beat schedule
│   │
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── discovery.py         # Apollo.io integration
│   │   ├── research.py          # Firecrawl + Claude extraction
│   │   ├── drafting.py          # Claude email generation
│   │   └── logistics.py         # Instantly.io sequencing
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app entrypoint
│   │   └── routes/
│   │       └── dashboard.py     # GET /, POST /leads/{id}/approve|reject
│   │
│   └── prompts/
│       ├── extraction.py        # Prompt for research extraction
│       └── email_draft.py       # Prompt for email drafting
│
├── migrations/
│   └── 001_initial.sql          # Table definitions
│
└── tests/
    ├── test_discovery.py
    ├── test_research.py
    ├── test_drafting.py
    └── test_logistics.py
```

---

## Data Model

```sql
-- State machine for the full pipeline
CREATE TABLE leads (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name  TEXT NOT NULL,
    founder_name  TEXT,
    founder_email TEXT,
    linkedin_url  TEXT,
    company_url   TEXT,
    funding_stage TEXT,           -- "seed", "series_a", "series_b"
    headcount     INT,
    status        TEXT NOT NULL DEFAULT 'found',
    -- Lifecycle: found → researched → drafted → approved|rejected → sent
    error_msg     TEXT,           -- set if a worker fails
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Output of the research worker
CREATE TABLE research (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id       UUID REFERENCES leads(id) ON DELETE CASCADE,
    tech_stack    JSONB,          -- e.g. ["Go", "Kafka", "Postgres"]
    recent_news   TEXT,           -- latest funding round, product launch, blog post
    hook          TEXT,           -- the specific angle for the email
    raw_content   TEXT,           -- full scraped markdown, for debugging
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Output of the drafting worker
CREATE TABLE drafts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id         UUID REFERENCES leads(id) ON DELETE CASCADE,
    subject_line    TEXT NOT NULL,
    body            TEXT NOT NULL,
    version         INT DEFAULT 1,    -- increments on regeneration
    reviewer_notes  TEXT,             -- your notes when reviewing
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

The `status` field is the contract between workers. Each worker queries `WHERE status = 'X'`, does its work, and writes the next status. `error_msg` captures worker failures without requiring log archaeology.

---

## Worker Designs

### 1. Discovery Worker (`workers/discovery.py`)

**Trigger:** Celery Beat schedule — every Monday at 08:00 local time.

**Inputs (configured in `.env`):**
- `APOLLO_API_KEY`
- `DISCOVERY_FUNDING_STAGES` — e.g. `["seed", "series_a"]`
- `DISCOVERY_INDUSTRIES` — e.g. `["fintech", "financial services"]`
- `DISCOVERY_HEADCOUNT_MAX` — e.g. `150`
- `DISCOVERY_LEADS_PER_RUN` — e.g. `20`

**Logic:**
1. Call Apollo `/mixed_companies/search` with funding + industry + headcount filters.
2. For each company, call Apollo `/people/search` filtered by title keywords (`CTO`, `VP Engineering`, `Engineering Manager`, `Founder`). Take the first match.
3. Deduplicate against existing `leads` rows (check by `founder_email` or `company_url`).
4. Insert new leads with `status=found`.
5. For each inserted lead, call `research_task.delay(lead_id)` — fire and forget, do not chain synchronously.

**Error handling:** If Apollo returns an error or rate limit, log and skip that batch. Do not crash the task. Retry via Celery's `autoretry_for=(Exception,)` with exponential backoff (max 3 retries).

---

### 2. Research Worker (`workers/research.py`)

**Trigger:** Dispatched by discovery worker per lead.

**Inputs:**
- `lead_id` (UUID)
- `FIRECRAWL_API_KEY`
- `ANTHROPIC_API_KEY`

**Logic:**
1. Fetch lead from DB.
2. Call Firecrawl `/scrape` on `company_url`. If the company has a `/blog` or `/engineering` path, scrape that too (up to 3 pages).
3. Concatenate scraped markdown. Truncate to ~8,000 tokens if needed.
4. Call Claude with the **extraction prompt** (see Prompts section). Request structured JSON output via tool use.
5. Parse JSON: `{ "tech_stack": [...], "recent_news": "...", "hook": "..." }`.
6. Insert row into `research` table.
7. Update `lead.status = researched`.
8. Call `drafting_task.delay(lead_id)`.

**Error handling:** If Firecrawl fails (site down, anti-bot), fall back to a basic HTTP GET + BeautifulSoup parse. If Claude fails, retry up to 3 times. If all fail, set `lead.error_msg` and `lead.status = found` (so it can be retried later).

---

### 3. Drafting Worker (`workers/drafting.py`)

**Trigger:** Dispatched by research worker per lead.

**Inputs:**
- `lead_id` (UUID)
- `ANTHROPIC_API_KEY`
- `SENDER_NAME`, `SENDER_ROLE`, `SENDER_COMPANY` — configurable so the agent is reusable

**Logic:**
1. Fetch lead + research from DB.
2. Call Claude with the **email draft prompt** (see Prompts section), passing: sender context, lead context, research hook, tech stack, recent news.
3. Claude returns `{ "subject": "...", "body": "..." }`.
4. Insert row into `drafts` table.
5. Update `lead.status = drafted`.
6. **Stop.** No automatic chaining — HITL pause.

**Email prompt rules (enforced in system prompt):**
- No openers like "I hope this finds you well" or "My name is X and I'm reaching out"
- First sentence must reference something specific from research (blog post, tech stack, recent news)
- Body must be ≤ 150 words
- One clear ask in the last sentence (e.g., "Would you be open to a 20-minute call?")
- Tone: peer-to-peer, not applicant-to-gatekeeper

---

### 4. Logistics Worker (`workers/logistics.py`)

**Trigger:** FastAPI `POST /leads/{id}/approve` endpoint calls `logistics_task.delay(lead_id)`.

**Inputs:**
- `lead_id` (UUID)
- `INSTANTLY_API_KEY`
- `INSTANTLY_CAMPAIGN_ID` — the email sequence to add leads to

**Logic:**
1. Fetch lead + most recent draft from DB.
2. Call Instantly API to upsert the lead (name, email, company).
3. Call Instantly API to add lead to the campaign with the draft as the first email in the sequence.
4. Instantly handles the 3-day drip timing and domain warmup.
5. Update `lead.status = sent`.

**Error handling:** If Instantly rejects the lead (invalid email, bounce risk), set `lead.status = rejected` with `error_msg`. Retry transient API errors up to 3 times.

---

## Prompts

### Extraction Prompt (`prompts/extraction.py`)

**System:**
> You are a research assistant for a software engineer at a fintech company. Given scraped content from a company's website, extract structured information for a targeted cold email. Return a JSON object only, no prose.

**User:**
> Company: {company_name}
> Scraped content:
> ---
> {scraped_content}
> ---
> Extract:
> - `tech_stack`: list of technologies mentioned or strongly implied (languages, databases, infrastructure)
> - `recent_news`: one sentence describing the most recent notable thing (funding, product, engineering blog topic)
> - `hook`: one specific, concrete angle for a cold email from a fintech engineer with ledger/payment infrastructure experience — what problem might they be facing that this person could help with?

Uses Claude **tool use** to enforce JSON schema.

---

### Email Draft Prompt (`prompts/email_draft.py`)

**System:**
> You write cold emails for software engineers reaching out to potential employers. The emails are peer-to-peer, specific, and short. You never use filler openers. You always reference something specific from research. You always end with one clear ask.
>
> Rules:
> - No "I hope this email finds you well" or similar openers
> - First sentence must reference a specific detail from the research
> - Body ≤ 150 words total
> - One ask in the final sentence only
> - Tone: confident peer, not job applicant
> - Do not mention "internship" or "opportunity" — just propose a conversation

**User:**
> Sender: {sender_name}, {sender_role} at {sender_company}
> Recipient: {founder_name}, {founder_title} at {company_name}
> Tech stack: {tech_stack}
> Recent news: {recent_news}
> Hook: {hook}
>
> Write the subject line and email body.

Uses Claude tool use to return `{ "subject": string, "body": string }`.

---

## FastAPI Dashboard

Minimal server — no React, no build step. Python + Jinja2 templates.

**Routes:**
- `GET /` — renders an HTML table of all leads with `status=drafted`. Shows company name, founder, subject line preview, and email body. Approve / Reject buttons per row.
- `POST /leads/{id}/approve` — updates `status=approved`, fires `logistics_task.delay(lead_id)`. Redirects back to `/`.
- `POST /leads/{id}/reject?notes=...` — updates `status=rejected`, stores notes. Redirects back to `/`.
- `POST /leads/{id}/regenerate` — updates the lead's draft version, re-dispatches `drafting_task.delay(lead_id)`. Redirects back to `/`.

Run alongside Celery workers: `uvicorn cold_email.api.main:app --reload`

---

## Infrastructure (`docker-compose.yml`)

```yaml
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: cold_email
      POSTGRES_USER: cold_email
      POSTGRES_PASSWORD: secret
    ports: ["5432:5432"]
    volumes:
      - ./migrations/001_initial.sql:/docker-entrypoint-initdb.d/001.sql
```

---

## Configuration (`.env.example`)

```
# Database
DATABASE_URL=postgresql+asyncpg://cold_email:secret@localhost:5432/cold_email

# Redis
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# APIs
APOLLO_API_KEY=
FIRECRAWL_API_KEY=
ANTHROPIC_API_KEY=
INSTANTLY_API_KEY=
INSTANTLY_CAMPAIGN_ID=

# Discovery config
DISCOVERY_FUNDING_STAGES=seed,series_a
DISCOVERY_INDUSTRIES=fintech,financial services
DISCOVERY_HEADCOUNT_MAX=150
DISCOVERY_LEADS_PER_RUN=20

# Sender identity
SENDER_NAME=Liyu Xiao
SENDER_ROLE=Software Engineer, Ledger Team
SENDER_COMPANY=Wealthsimple
```

---

## Running the System

```bash
# 1. Start infrastructure
docker compose up -d

# 2. Install dependencies
uv sync

# 3. Apply migrations
psql $DATABASE_URL -f migrations/001_initial.sql

# 4. Start Celery worker
uv run celery -A cold_email.celery_app worker --loglevel=info

# 5. Start Celery Beat scheduler
uv run celery -A cold_email.celery_app beat --loglevel=info

# 6. Start dashboard
uv run uvicorn cold_email.api.main:app --reload --port 8000

# 7. (Optional) Trigger discovery manually for testing
uv run python -c "from cold_email.workers.discovery import discovery_task; discovery_task.delay()"
```

---

## Verification Plan

| Step | How to verify |
|------|--------------|
| Infrastructure | `docker compose ps` shows redis + postgres healthy |
| Database | Connect to Postgres, run `SELECT * FROM leads LIMIT 5` after triggering discovery |
| Discovery | Manually call `discovery_task.delay()`, check Celery logs + leads table |
| Research | Check `research` table after discovery; verify `hook` field is specific |
| Drafting | Check `drafts` table; read the email — does it reference real company details? |
| HITL dashboard | Visit `localhost:8000`, verify drafted leads appear with email preview |
| Approve flow | Click Approve, verify `lead.status = sent` in DB and Instantly campaign has the lead |
| End-to-end | Run full pipeline on 3 real companies; read the 3 drafts; approve 1; verify Instantly sequence |

---

## Dependencies

```toml
[project]
name = "cold-email-agent"
version = "0.1.0"
requires-python = ">=3.12"

dependencies = [
    "celery[redis]>=5.3",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "fastapi>=0.111",
    "uvicorn>=0.30",
    "httpx>=0.27",           # async HTTP client for API calls
    "anthropic>=0.28",       # Claude API
    "firecrawl-py>=0.0.16",  # Firecrawl SDK
    "pydantic-settings>=2.3",
    "jinja2>=3.1",           # dashboard templates
    "python-dotenv>=1.0",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-celery>=1.0",
]
```
