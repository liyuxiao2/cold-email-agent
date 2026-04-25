# Cold Email Agent — Claude Instructions

## Project Purpose

Autonomous cold email pipeline: scrape early-stage fintech startups from [startups.gallery](https://startups.gallery), research each company with Firecrawl + Claude (including enriching founder contact info), draft personalized outreach, hold for human review, then schedule delivery via Instantly.io.

The explicit goal of this project is **learning**. Liyu is building this to understand async Python, Celery task queues, LLM tool use, and external API orchestration — not just to ship something that works.

---

## Role Split: What Claude Does vs. What Liyu Does

### Claude handles autonomously (no explanation needed):

- All boilerplate: `pyproject.toml`, `docker-compose.yml`, `.env.example`, `__init__.py` files, migration SQL
- FastAPI routes, Jinja2 templates, HTML dashboard — all frontend/server plumbing
- SQLAlchemy model definitions and async engine setup
- Celery app configuration and Beat schedule wiring
- `pydantic-settings` config class
- Test scaffolding and fixtures
- Import statements, type hints on new code

### Claude teaches while implementing:

- **Business logic** — explain _why_ the logic is structured the way it is, not just _what_ it does
- **Async Python patterns** — when to use `async def`, when `await` is required, what the event loop is doing
- **Celery concepts** — what `delay()` vs `apply_async()` means, how chaining works, what the broker vs backend is for, how Beat scheduling maps to cron
- **Claude API patterns** — tool use for structured output, prompt caching, why we truncate to ~8k tokens
- **State machine logic** — why the `status` field is the contract between workers, what happens if a worker crashes mid-execution
- **Error handling decisions** — why exponential backoff, why we set `error_msg` instead of raising, when to retry vs give up

---

## Teaching Style

When implementing business logic, do this inline in the code using comments where the concept isn't obvious from the code alone. For non-trivial patterns, add a short explanation in your response _before_ showing the code — a "here's what we're doing and why" paragraph. Keep it concise: 2–4 sentences max per concept, then show the implementation.

**Point to documentation** when introducing a library or API for the first time. Format: a sentence explaining the concept, then a link. Example:

> Celery chains let you compose tasks so the output of one feeds into the next — [Celery chains docs](https://docs.celeryq.dev/en/stable/userguide/canvas.html#chains).

Do not explain things Liyu already knows (Python basics, REST APIs, git). Do explain things that are genuinely new in this project's context.

---

## Architecture

```
Celery Beat (Monday 8am)
    │
    ▼
discovery_task ──▶ startups.gallery (scraped via Firecrawl)
    │  (inserts leads with company_name, company_url, funding_stage; status=found)
    │
    └──▶ research_task ──▶ Firecrawl + Claude + Hunter.io fallback
              │  (scrapes /about /team, enriches founder_name/email,
              │   extracts tech_stack + hook; status=researched)
              │
              └──▶ drafting_task ──▶ Claude (email generation)
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

Workers never talk to each other directly. All state lives in Postgres; all task dispatch goes through Redis.

**Note on contact enrichment:** Since startups.gallery doesn't expose founder info, `leads` are inserted by discovery with `founder_name` and `founder_email` as NULL. The research worker is responsible for filling those in (scrape the company's team/about page → regex email pattern → fall back to Hunter.io) before doing the hook extraction. The state machine stays the same — enrichment is folded into `research_task` rather than being its own status.

---

## Project Structure

```
cold-email-agent/
├── docker-compose.yml           # Redis + Postgres
├── pyproject.toml               # uv-managed dependencies
├── .env.example                 # API keys template
│
├── cold_email/
│   ├── config.py                # pydantic-settings — loads .env
│   ├── database.py              # SQLAlchemy models + async engine
│   ├── celery_app.py            # Celery app + Beat schedule
│   │
│   ├── workers/
│   │   ├── discovery.py         # startups.gallery scrape via Firecrawl
│   │   ├── research.py          # Firecrawl + Claude extraction + Hunter.io enrichment
│   │   ├── drafting.py          # Claude email generation
│   │   └── logistics.py         # Instantly.io sequencing
│   │
│   ├── api/
│   │   ├── main.py              # FastAPI app entrypoint
│   │   └── routes/
│   │       └── dashboard.py     # GET /, POST /leads/{id}/approve|reject
│   │
│   └── prompts/
│       ├── extraction.py        # Structured extraction prompt
│       └── email_draft.py       # Email drafting prompt
│
├── migrations/
│   └── 001_initial.sql
└── tests/
```

---

## Data Model

Lead `status` is the pipeline's state machine:
`found → researched → drafted → approved|rejected → sent`

Each worker queries `WHERE status = 'X'`, does its work, writes the next status. If a worker fails mid-execution, `error_msg` captures why without requiring log archaeology.

```sql
CREATE TABLE leads (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name  TEXT NOT NULL,
    founder_name  TEXT,
    founder_email TEXT,
    linkedin_url  TEXT,
    company_url   TEXT,
    funding_stage TEXT,
    headcount     INT,
    status        TEXT NOT NULL DEFAULT 'found',
    error_msg     TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE research (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id       UUID REFERENCES leads(id) ON DELETE CASCADE,
    tech_stack    JSONB,
    recent_news   TEXT,
    hook          TEXT,
    raw_content   TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE drafts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id         UUID REFERENCES leads(id) ON DELETE CASCADE,
    subject_line    TEXT NOT NULL,
    body            TEXT NOT NULL,
    version         INT DEFAULT 1,
    reviewer_notes  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Tech Stack & Key Docs

| Layer          | Library              | Docs                                                                              |
| -------------- | -------------------- | --------------------------------------------------------------------------------- |
| Task queue     | Celery + Redis       | [Celery docs](https://docs.celeryq.dev/en/stable/)                                |
| Beat scheduler | Celery Beat          | [Beat docs](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html)     |
| Database ORM   | SQLAlchemy 2.0 async | [Async SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) |
| DB driver      | asyncpg              | [asyncpg docs](https://magicstack.github.io/asyncpg/current/)                     |
| Web server     | FastAPI              | [FastAPI docs](https://fastapi.tiangolo.com/)                                     |
| HTTP client    | httpx                | [httpx async](https://www.python-httpx.org/async/)                                |
| LLM            | Anthropic Python SDK | [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python)        |
| Web scraping   | firecrawl-py         | [Firecrawl docs](https://docs.firecrawl.dev/)                                     |
| Config         | pydantic-settings    | [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) |
| Packaging      | uv                   | [uv docs](https://docs.astral.sh/uv/)                                             |

External APIs:

- [startups.gallery](https://startups.gallery) — curated early-stage startup directory (no public API; scraped via Firecrawl)
- [Firecrawl API](https://docs.firecrawl.dev/api-reference/introduction) — web scraping (discovery source + per-company research)
- [Hunter.io API](https://hunter.io/api-documentation/v2) — email finder fallback when pattern-matching from team pages fails
- [Instantly.io API](https://developer.instantly.ai/) — email sequencing

---

## Claude API Conventions

This project uses Claude for two distinct tasks: **structured extraction** (tool use to enforce JSON schema) and **email drafting** (tool use to return `{subject, body}`).

Use `claude-sonnet-4-6` for both. Use tool use (not JSON mode) to enforce output schema — it's more reliable. Enable prompt caching on the system prompt for the research worker since the same system prompt is called once per lead.

Example pattern for tool use in this codebase:

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=[{...}],          # defines the JSON schema
    tool_choice={"type": "any"},  # forces Claude to use the tool
    messages=[...]
)
result = response.content[0].input  # the structured dict
```

---

## Celery Conventions

- Workers are `@app.task(bind=True, autoretry_for=(Exception,), max_retries=3, default_retry_delay=60)` — the `bind=True` gives access to `self` for manual retry control.
- Tasks dispatch other tasks with `.delay(lead_id)` — pass only UUIDs between tasks, never ORM objects (they don't serialize).
- All DB access inside tasks must be sync (Celery workers run in a sync context). Use a sync SQLAlchemy session, not async, inside tasks.
- Beat schedule lives in `celery_app.py` as `app.conf.beat_schedule`.

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

# 7. Trigger discovery manually for testing
uv run python -c "from cold_email.workers.discovery import discovery_task; discovery_task.delay()"
```

---

## Verification Checkpoints

| Step           | How to verify                                                              |
| -------------- | -------------------------------------------------------------------------- |
| Infrastructure | `docker compose ps` — redis + postgres healthy                             |
| Database       | `SELECT * FROM leads LIMIT 5` after triggering discovery                   |
| Discovery      | `discovery_task.delay()` → check Celery logs + leads table                 |
| Research       | Check `research` table; verify `hook` is specific and concrete             |
| Drafting       | Check `drafts` table; does the email reference real company details?       |
| Dashboard      | `localhost:8000` — drafted leads appear with email preview                 |
| Approve flow   | Click Approve → `lead.status = sent` in DB + lead in Instantly campaign    |
| End-to-end     | Run on 3 real companies; read drafts; approve 1; verify Instantly sequence |
