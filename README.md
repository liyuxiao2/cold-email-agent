# Cold Email Agent

Autonomous cold email pipeline that discovers early-stage startups, researches founders and a pool of emailable contacts, drafts personalized outreach, and sends via the Gmail API after human review.

## Pipeline

The pipeline is split into a GLOBAL half (companies, research, contacts —
admin-populated, shared by every user) and a PER-USER half (outreach, drafts —
one user's attempt to reach one company). See `CLAUDE.md` and
`docs/architecture-flow.md` for the full two-level model and Mermaid diagrams.

```
Celery Beat (Monday 8am)
    |
    v
discovery_task --> Firecrawl Extract (startups.gallery, YC, etc.)
    |  inserts companies with research_status='found'
    |
    +---> research_task --> scrape + LLM (Groq/Gemini) + Hunter Domain Search
              |  enriches company info, extracts hook, saves a contact pool;
              |  research_status='researched' -> joins the shared pool

[User browses the pool, selects companies] --> POST /api/outreach
    |  select_contact() picks the least-globally-contacted eligible contact
    |  under a per-contact cap (spreading); outreach.status='queued'
    |
    +---> drafting_task(user_id) --> LLM (Groq/Gemini, automatic failover,
              |                       rate-limited by a fleet-wide token bucket)
              |  generates email for that user's queued outreach rows;
              |  outreach.status='drafted'
              |
        [Human review via dashboard]
              |
        logistics_task --> Gmail API
              outreach.status='sent'
```

Drafting is on-demand, not a timed sweep — it fires the moment a user selects
companies. An hourly Beat job (`drafting_recovery_task`) only re-dispatches it
for users whose original dispatch appears to have been lost.

### User flow

1. **Sign in with Google.** One consent screen grants identity and Gmail send
   access together.
2. **Upload a résumé** (optional) during onboarding — it's parsed into a
   suggested profile you review and save before anything is used to draft.
3. **Browse the pool** (`/pool`) — the shared, researched companies you
   haven't already targeted, with filters and a running quota bar.
4. **Select companies and submit.** Each selected company is routed to a
   least-contacted eligible contact and queued; drafting starts immediately.
5. **Review drafts** in the dashboard's review queue.
6. **Approve** to send via Gmail, or reject / regenerate.

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
- `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` - the OAuth2 *application's* credentials, not a mailbox's (see [Google OAuth setup](#google-oauth-setup) below). Every user connects their own mailbox by signing in with Google; there is no separate script or per-mailbox credential to mint.
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

### First sign-in: onboarding

There's no `scripts/gmail_auth.py` to run and no compiled-in sender identity
to edit — every user sets up their own by signing in:

1. **Sign in with Google.** The consent screen grants both identity and
   `gmail.compose` in one flow, so this is also how each user connects their
   own Gmail account.
2. **Upload a résumé** (optional — you can skip and fill in manually). It's
   parsed into a *suggested* profile; nothing is saved yet.
3. **Review the extracted profile** — name, intro, links, and experience
   bullets — and edit anything the extraction got wrong.
4. **Save.** That's the first `PUT /api/profile`; from then on, drafting uses
   this profile, this résumé, and this Gmail connection to draft and send on
   that user's behalf.

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
  database.py          # SQLAlchemy models (Company, CompanyContact, Outreach, ...) + sync/async engines
  celery_app.py        # Celery app + Beat schedule
  workers/
    discovery/
      discovery.py      # Firecrawl Extract -> find companies
    research/
      research.py       # Celery orchestration; helpers/ has scraping, LLM, Hunter Domain Search
    drafting/
      drafting.py        # Celery orchestration; helpers/ has LLM generation, DB writes
    logistics/
      logistics.py       # Gmail API -> send emails
    shared/
      llm.py             # provider-agnostic generate_json (Groq + Gemini, automatic failover)
      errors.py           # fail_company / fail_outreach -> dead_letter
  api/
    main.py            # FastAPI app
    routes/
      outreach.py       # per-user outreach routes (review, approve/reject/regenerate)
      companies.py      # read-only global company pool
      pipeline.py        # discovery/drafting/research triggers + stats
      dlq.py              # dead-letter queue list/retry
```

## Verification

| Step | How to verify |
|------|--------------|
| Infrastructure | `docker compose ps` - redis + postgres healthy |
| Database | `psql $DATABASE_URL -c "SELECT * FROM companies LIMIT 5"` |
| Discovery | Trigger manually, check Celery logs + `companies` table |
| Research | Check `research` table for `hook` values and `company_contacts` for the contact pool |
| Drafting | Check `drafts` table for generated emails |
| Dashboard | `localhost:3000` - drafted outreach rows appear in the review queue |

## Tech Stack

| Layer | Library |
|-------|---------|
| Task queue | Celery + Redis |
| Database | SQLAlchemy 2.0 + asyncpg/psycopg2 |
| Web server | FastAPI |
| LLM | Groq + Google Gemini, provider-agnostic layer with automatic failover |
| Web scraping | Firecrawl |
| Contact discovery | Hunter.io Domain Search |
| Email delivery | Gmail API |
| Config | pydantic-settings |
| Packaging | uv |
