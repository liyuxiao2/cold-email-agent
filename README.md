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
- `GEMINI_API_KEY` - get from [aistudio.google.com](https://aistudio.google.com/apikey)
- `GROQ_API_KEY` - get from [console.groq.com](https://console.groq.com/keys)
- `HUNTER_API_KEY` - get from [hunter.io](https://hunter.io) (founder email discovery)
- `GMAIL_*` - OAuth2 client credentials for the shared sending mailbox (see [Google OAuth setup](#google-oauth-setup) below — the web consent flow now supersedes minting these with `scripts/gmail_auth.py`)
- Auth vars (`SESSION_SECRET`, `ENCRYPTION_KEY`, `GOOGLE_REDIRECT_URI`, `FRONTEND_URL`, `ADMIN_EMAIL`, `COOKIE_SECURE`, `CORS_ORIGINS`) - see [Google OAuth setup](#google-oauth-setup) below

### Google OAuth setup

Sign-in and sending both go through the same Google Cloud OAuth client — no separate credentials to provision.

1. **Add scopes to the existing OAuth client.** In the Google Cloud Console, under the OAuth consent screen's scopes, make sure these four are enabled:
   - `openid`
   - `email`
   - `profile`
   - `https://www.googleapis.com/auth/gmail.compose`
2. **Register the web redirect URI** on the OAuth client (Credentials -> your client -> Authorized redirect URIs):
   ```
   https://<backend>/api/auth/google/callback
   ```
   Use your actual backend host, e.g. `http://localhost:8080/...` locally or the Cloud Run URL in production — it must exactly match `GOOGLE_REDIRECT_URI`.
3. **Generate the two auth keys** and put them in `.env`:
   ```bash
   uv run python -c "import secrets; print(secrets.token_urlsafe(48))"          # SESSION_SECRET
   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # ENCRYPTION_KEY
   ```
4. **Set `ADMIN_EMAIL`** to the Google account that should be seeded as the first admin. It's created (or promoted, if it already exists) on every boot — see `scripts/seed_admin.py`.

`scripts/gmail_auth.py` still exists for minting a standalone Gmail refresh token, but the web consent flow above is now how users (including the sender mailbox owner) authenticate — you no longer need to run that script as part of setup.

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
