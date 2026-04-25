# Cold Email Agent

Autonomous cold email pipeline that discovers early-stage startups, researches founders, drafts personalized outreach, and sends via Instantly.io after human review.

## Pipeline

```
Celery Beat (Monday 8am)
    |
    v
discovery_task --> Firecrawl Extract (startups.gallery, YC, etc.)
    |  inserts leads with status='found'
    |
    +---> research_task --> Firecrawl + Claude + Hunter.io
              |  enriches founder info, extracts hook; status='researched'
              |
              +---> drafting_task --> Claude
                        |  generates email; status='drafted'
                        |
                  [Human review via dashboard]
                        |
                  logistics_task --> Instantly.io
                        status='sent'
```

## Prerequisites

- Python 3.12+
- Docker & Docker Compose
- [uv](https://docs.astral.sh/uv/) (Python package manager)

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/your-user/cold-email-agent.git
cd cold-email-agent
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your API keys:
- `FIRECRAWL_API_KEY` - get from [firecrawl.dev](https://firecrawl.dev)
- `ANTHROPIC_API_KEY` - get from [console.anthropic.com](https://console.anthropic.com)
- `INSTANTLY_API_KEY` - get from [instantly.ai](https://instantly.ai)

### 3. Run everything

```bash
make dev
```

This starts Docker (Redis + Postgres), the Celery worker, Beat scheduler, and FastAPI dashboard in one command. Postgres migration runs automatically on first start.

Other useful commands:

```bash
make discovery   # Trigger discovery manually
make test        # Run tests
make down        # Stop Docker containers
```

If you prefer separate terminals for cleaner logs, run `make worker`, `make beat`, and `make dashboard` individually.

## Project Structure

```
cold_email/
  config.py            # pydantic-settings, loads .env
  database.py          # SQLAlchemy models + sync/async engines
  celery_app.py        # Celery app + Beat schedule
  workers/
    discovery.py       # Firecrawl Extract -> find startups
    research.py        # Firecrawl + Claude -> enrich leads
    drafting.py        # Claude -> write emails
    logistics.py       # Instantly.io -> send emails
  api/
    main.py            # FastAPI app
    routes/
      dashboard.py     # Review & approve UI
```

## Verification

| Step | How to verify |
|------|--------------|
| Infrastructure | `docker compose ps` - redis + postgres healthy |
| Database | `psql $DATABASE_URL -c "SELECT * FROM leads LIMIT 5"` |
| Discovery | Trigger manually, check Celery logs + leads table |
| Research | Check `research` table for `hook` values |
| Drafting | Check `drafts` table for generated emails |
| Dashboard | `localhost:8000` - drafted leads appear |

## Tech Stack

| Layer | Library |
|-------|---------|
| Task queue | Celery + Redis |
| Database | SQLAlchemy 2.0 + asyncpg/psycopg2 |
| Web server | FastAPI |
| LLM | Anthropic Claude |
| Web scraping | Firecrawl |
| Email delivery | Instantly.io |
| Config | pydantic-settings |
| Packaging | uv |
