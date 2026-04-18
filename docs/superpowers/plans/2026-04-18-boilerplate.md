# Cold Email Agent — Boilerplate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold the complete project skeleton so the system is runnable end-to-end before business logic is written.

**Architecture:** Celery + Redis for async task dispatch, PostgreSQL + SQLAlchemy (sync for Celery workers, async for FastAPI) for persistent state, FastAPI + Jinja2 for the HITL review dashboard. Worker files are stubs with the correct task decorator structure but `raise NotImplementedError` bodies.

**Tech Stack:** Python 3.12, uv, Celery 5.3, SQLAlchemy 2.0, asyncpg, psycopg2-binary, FastAPI, Jinja2, pydantic-settings, anthropic

---

## File Map

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | uv-managed dependencies |
| `docker-compose.yml` | Redis + Postgres local infrastructure |
| `.env.example` | API key template |
| `.gitignore` | Exclude .env, __pycache__, .venv, etc. |
| `migrations/001_initial.sql` | leads, research, drafts table definitions |
| `cold_email/__init__.py` | Package marker |
| `cold_email/workers/__init__.py` | Package marker |
| `cold_email/api/__init__.py` | Package marker |
| `cold_email/api/routes/__init__.py` | Package marker |
| `cold_email/prompts/__init__.py` | Package marker |
| `tests/__init__.py` | Package marker |
| `cold_email/config.py` | pydantic-settings: loads .env, typed config singleton |
| `cold_email/database.py` | SQLAlchemy ORM models + async/sync engine factories |
| `cold_email/celery_app.py` | Celery app instance + Beat schedule |
| `cold_email/prompts/extraction.py` | Extraction prompt + tool schema + message builder |
| `cold_email/prompts/email_draft.py` | Email draft prompt + tool schema + message builder |
| `cold_email/workers/discovery.py` | Discovery task stub |
| `cold_email/workers/research.py` | Research task stub |
| `cold_email/workers/drafting.py` | Drafting task stub |
| `cold_email/workers/logistics.py` | Logistics task stub |
| `cold_email/api/main.py` | FastAPI app + Jinja2 mount |
| `cold_email/api/routes/dashboard.py` | GET /, POST approve/reject/regenerate |
| `cold_email/templates/dashboard.html` | Jinja2 HITL dashboard |
| `tests/conftest.py` | Pytest async session fixture |
| `tests/test_discovery.py` | Discovery test scaffold |
| `tests/test_research.py` | Research test scaffold |
| `tests/test_drafting.py` | Drafting test scaffold |
| `tests/test_logistics.py` | Logistics test scaffold |

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `.gitignore`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "cold-email-agent"
version = "0.1.0"
requires-python = ">=3.12"

dependencies = [
    "celery[redis]>=5.3",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "psycopg2-binary>=2.9",
    "fastapi>=0.111",
    "uvicorn>=0.30",
    "httpx>=0.27",
    "anthropic>=0.28",
    "firecrawl-py>=0.0.16",
    "pydantic-settings>=2.3",
    "jinja2>=3.1",
    "python-dotenv>=1.0",
    "beautifulsoup4>=4.12",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-celery>=1.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create `docker-compose.yml`**

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
      - postgres_data:/var/lib/postgresql/data
      - ./migrations/001_initial.sql:/docker-entrypoint-initdb.d/001.sql

volumes:
  postgres_data:
```

- [ ] **Step 3: Create `.env.example`**

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

- [ ] **Step 4: Create `.gitignore`**

```
.env
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
dist/
*.egg-info/
.DS_Store
uv.lock
```

- [ ] **Step 5: Install dependencies**

```bash
uv sync
```

Expected: dependencies resolved and installed, `uv.lock` created.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml docker-compose.yml .env.example .gitignore
git commit -m "chore: project scaffolding — pyproject, docker-compose, env template"
```

---

### Task 2: Database migration

**Files:**
- Create: `migrations/001_initial.sql`

- [ ] **Step 1: Create `migrations/001_initial.sql`**

```sql
CREATE TABLE IF NOT EXISTS leads (
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

CREATE TABLE IF NOT EXISTS research (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id       UUID REFERENCES leads(id) ON DELETE CASCADE,
    tech_stack    JSONB,
    recent_news   TEXT,
    hook          TEXT,
    raw_content   TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS drafts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id         UUID REFERENCES leads(id) ON DELETE CASCADE,
    subject_line    TEXT NOT NULL,
    body            TEXT NOT NULL,
    version         INT DEFAULT 1,
    reviewer_notes  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

- [ ] **Step 2: Start infrastructure and apply migration**

```bash
docker compose up -d
sleep 3
psql postgresql://cold_email:secret@localhost:5432/cold_email -f migrations/001_initial.sql
```

Expected: `CREATE TABLE` printed three times.

- [ ] **Step 3: Verify tables exist**

```bash
psql postgresql://cold_email:secret@localhost:5432/cold_email -c "\dt"
```

Expected: `leads`, `research`, `drafts` listed.

- [ ] **Step 4: Commit**

```bash
git add migrations/
git commit -m "chore: initial SQL migration — leads, research, drafts tables"
```

---

### Task 3: Package structure

**Files:**
- Create: `cold_email/__init__.py` (empty)
- Create: `cold_email/workers/__init__.py` (empty)
- Create: `cold_email/api/__init__.py` (empty)
- Create: `cold_email/api/routes/__init__.py` (empty)
- Create: `cold_email/prompts/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)

- [ ] **Step 1: Create directories and empty `__init__.py` files**

```bash
mkdir -p cold_email/workers cold_email/api/routes cold_email/prompts cold_email/templates migrations tests
touch cold_email/__init__.py
touch cold_email/workers/__init__.py
touch cold_email/api/__init__.py
touch cold_email/api/routes/__init__.py
touch cold_email/prompts/__init__.py
touch tests/__init__.py
```

- [ ] **Step 2: Commit**

```bash
git add cold_email/ tests/__init__.py
git commit -m "chore: create package directory structure"
```

---

### Task 4: Config

**Files:**
- Create: `cold_email/config.py`

- [ ] **Step 1: Create `cold_email/config.py`**

pydantic-settings reads `.env` automatically and maps env var names to field names (case-insensitive). The `sync_database_url` computed field derives the psycopg2 URL from the asyncpg one — Celery workers run in a sync context and need the psycopg2 driver.

```python
from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # asyncpg for FastAPI, psycopg2 derived below for Celery workers
    database_url: str = "postgresql+asyncpg://cold_email:secret@localhost:5432/cold_email"

    @computed_field
    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg2")

    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    apollo_api_key: str = ""
    firecrawl_api_key: str = ""
    anthropic_api_key: str = ""
    instantly_api_key: str = ""
    instantly_campaign_id: str = ""

    discovery_funding_stages: list[str] = ["seed", "series_a"]
    discovery_industries: list[str] = ["fintech", "financial services"]
    discovery_headcount_max: int = 150
    discovery_leads_per_run: int = 20

    sender_name: str = "Liyu Xiao"
    sender_role: str = "Software Engineer, Ledger Team"
    sender_company: str = "Wealthsimple"


settings = Settings()
```

- [ ] **Step 2: Verify settings load**

```bash
uv run python -c "from cold_email.config import settings; print(settings.sender_name, settings.sync_database_url)"
```

Expected: `Liyu Xiao postgresql+psycopg2://cold_email:secret@localhost:5432/cold_email`

- [ ] **Step 3: Commit**

```bash
git add cold_email/config.py
git commit -m "chore: pydantic-settings config with computed sync DB URL"
```

---

### Task 5: Database models

**Files:**
- Create: `cold_email/database.py`

- [ ] **Step 1: Create `cold_email/database.py`**

SQLAlchemy 2.0 lets you define one set of model classes and use them with both sync and async sessions — the ORM is the same, only the engine and session type differ.

```python
import uuid
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cold_email.config import settings


class Base(DeclarativeBase):
    pass


class Lead(Base):
    __tablename__ = "leads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name = Column(String, nullable=False)
    founder_name = Column(String)
    founder_email = Column(String)
    linkedin_url = Column(String)
    company_url = Column(String)
    funding_stage = Column(String)
    headcount = Column(Integer)
    status = Column(String, nullable=False, default="found")
    error_msg = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    research = relationship("Research", back_populates="lead", cascade="all, delete-orphan")
    drafts = relationship("Draft", back_populates="lead", cascade="all, delete-orphan")


class Research(Base):
    __tablename__ = "research"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"))
    tech_stack = Column(JSONB)
    recent_news = Column(Text)
    hook = Column(Text)
    raw_content = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="research")


class Draft(Base):
    __tablename__ = "drafts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"))
    subject_line = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    version = Column(Integer, default=1)
    reviewer_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="drafts")


# Async engine — FastAPI uses this
async_engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

# Sync engine — Celery workers use this
sync_engine = create_engine(settings.sync_database_url, echo=False)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False)


async def get_async_session() -> AsyncSession:
    """FastAPI dependency — yields an async session per request."""
    async with AsyncSessionLocal() as session:
        yield session


def get_sync_session():
    """Celery helper — yields a sync session per task."""
    with SyncSessionLocal() as session:
        yield session
```

- [ ] **Step 2: Verify models import**

```bash
uv run python -c "from cold_email.database import Lead, Research, Draft; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add cold_email/database.py
git commit -m "chore: SQLAlchemy models and async/sync engine setup"
```

---

### Task 6: Celery app

**Files:**
- Create: `cold_email/celery_app.py`

- [ ] **Step 1: Create `cold_email/celery_app.py`**

`autodiscover_tasks` scans the workers package and registers any `@shared_task` decorated functions. The Beat schedule maps to a standard cron: Monday at 08:00 Toronto time.

```python
from celery import Celery
from celery.schedules import crontab

from cold_email.config import settings

app = Celery("cold_email")

app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Toronto",
    enable_utc=True,
)

app.conf.beat_schedule = {
    "discovery-every-monday": {
        "task": "cold_email.workers.discovery.discovery_task",
        "schedule": crontab(hour=8, minute=0, day_of_week="monday"),
    }
}

app.autodiscover_tasks(["cold_email.workers"])
```

- [ ] **Step 2: Verify Celery app loads and discovers tasks**

```bash
uv run python -c "
from cold_email.celery_app import app
app.loader.import_default_modules()
tasks = [k for k in app.tasks if not k.startswith('celery.')]
print(tasks)
"
```

Expected: list includes the four worker task names (will be empty until Task 8 stubs are created).

- [ ] **Step 3: Commit**

```bash
git add cold_email/celery_app.py
git commit -m "chore: Celery app config and Beat schedule"
```

---

### Task 7: Prompts

**Files:**
- Create: `cold_email/prompts/extraction.py`
- Create: `cold_email/prompts/email_draft.py`

- [ ] **Step 1: Create `cold_email/prompts/extraction.py`**

The tool schema tells Claude exactly what JSON shape to return. Using `tool_choice={"type": "any"}` forces Claude to call `extract_research`, so the response always has a structured `input` dict — no JSON parsing of free text needed.

```python
EXTRACTION_SYSTEM = (
    "You are a research assistant for a software engineer at a fintech company. "
    "Given scraped content from a company's website, extract structured information "
    "for a targeted cold email. Return a JSON object only, no prose."
)

EXTRACTION_TOOL = {
    "name": "extract_research",
    "description": "Extract structured research from company website content",
    "input_schema": {
        "type": "object",
        "properties": {
            "tech_stack": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Technologies mentioned or strongly implied "
                    "(languages, databases, infrastructure)"
                ),
            },
            "recent_news": {
                "type": "string",
                "description": (
                    "One sentence describing the most recent notable thing "
                    "(funding, product launch, engineering blog topic)"
                ),
            },
            "hook": {
                "type": "string",
                "description": (
                    "One specific, concrete angle for a cold email from a fintech engineer "
                    "with ledger/payment infrastructure experience — what problem might they "
                    "be facing that this person could help with?"
                ),
            },
        },
        "required": ["tech_stack", "recent_news", "hook"],
    },
}


def build_extraction_messages(company_name: str, scraped_content: str) -> list[dict]:
    return [
        {
            "role": "user",
            "content": (
                f"Company: {company_name}\n"
                f"Scraped content:\n---\n{scraped_content}\n---\n"
                "Extract the research fields."
            ),
        }
    ]
```

- [ ] **Step 2: Create `cold_email/prompts/email_draft.py`**

```python
EMAIL_DRAFT_SYSTEM = (
    "You write cold emails for software engineers reaching out to potential employers. "
    "The emails are peer-to-peer, specific, and short. You never use filler openers. "
    "You always reference something specific from research. "
    "You always end with one clear ask.\n\n"
    "Rules:\n"
    "- No 'I hope this email finds you well' or similar openers\n"
    "- First sentence must reference a specific detail from the research\n"
    "- Body ≤ 150 words total\n"
    "- One ask in the final sentence only\n"
    "- Tone: confident peer, not job applicant\n"
    "- Do not mention 'internship' or 'opportunity' — just propose a conversation"
)

EMAIL_DRAFT_TOOL = {
    "name": "write_email",
    "description": "Write a cold email subject line and body",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Email subject line",
            },
            "body": {
                "type": "string",
                "description": "Email body, ≤ 150 words",
            },
        },
        "required": ["subject", "body"],
    },
}


def build_email_draft_messages(
    sender_name: str,
    sender_role: str,
    sender_company: str,
    founder_name: str,
    founder_title: str,
    company_name: str,
    tech_stack: list[str],
    recent_news: str,
    hook: str,
) -> list[dict]:
    return [
        {
            "role": "user",
            "content": (
                f"Sender: {sender_name}, {sender_role} at {sender_company}\n"
                f"Recipient: {founder_name}, {founder_title} at {company_name}\n"
                f"Tech stack: {', '.join(tech_stack)}\n"
                f"Recent news: {recent_news}\n"
                f"Hook: {hook}\n\n"
                "Write the subject line and email body."
            ),
        }
    ]
```

- [ ] **Step 3: Verify prompts import**

```bash
uv run python -c "
from cold_email.prompts.extraction import EXTRACTION_TOOL, build_extraction_messages
from cold_email.prompts.email_draft import EMAIL_DRAFT_TOOL, build_email_draft_messages
print('OK')
"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add cold_email/prompts/
git commit -m "chore: extraction and email draft prompts with Claude tool schemas"
```

---

### Task 8: Worker stubs

**Files:**
- Create: `cold_email/workers/discovery.py`
- Create: `cold_email/workers/research.py`
- Create: `cold_email/workers/drafting.py`
- Create: `cold_email/workers/logistics.py`

Each stub uses the real `@shared_task` decorator with the production retry config so the structure is correct and Celery can discover/register the tasks now. Business logic is `raise NotImplementedError` — that gets replaced when each worker is implemented.

- [ ] **Step 1: Create `cold_email/workers/discovery.py`**

```python
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    name="cold_email.workers.discovery.discovery_task",
)
def discovery_task(self) -> dict:
    """
    Triggered by Celery Beat every Monday at 08:00.
    Steps (to be implemented):
      1. Query Apollo.io for early-stage fintech companies
      2. Find technical founder/CTO contact per company
      3. Deduplicate against existing leads by founder_email / company_url
      4. Insert new leads with status='found'
      5. Dispatch research_task.delay(lead_id) per new lead
    """
    raise NotImplementedError("Discovery worker not yet implemented")
```

- [ ] **Step 2: Create `cold_email/workers/research.py`**

```python
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    name="cold_email.workers.research.research_task",
)
def research_task(self, lead_id: str) -> dict:
    """
    Dispatched by discovery_task per lead.
    Steps (to be implemented):
      1. Fetch lead from DB
      2. Scrape company_url with Firecrawl (+ /blog, /engineering up to 3 pages)
      3. Truncate concatenated markdown to ~8,000 tokens
      4. Call Claude with extraction prompt (tool use → structured JSON)
      5. Insert row into research table
      6. Update lead.status = 'researched'
      7. Dispatch drafting_task.delay(lead_id)
    """
    raise NotImplementedError("Research worker not yet implemented")
```

- [ ] **Step 3: Create `cold_email/workers/drafting.py`**

```python
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    name="cold_email.workers.drafting.drafting_task",
)
def drafting_task(self, lead_id: str) -> dict:
    """
    Dispatched by research_task per lead.
    Steps (to be implemented):
      1. Fetch lead + research from DB
      2. Call Claude with email draft prompt (tool use → {subject, body})
      3. Insert row into drafts table
      4. Update lead.status = 'drafted'
      5. Stop — HITL pause, no further chaining
    """
    raise NotImplementedError("Drafting worker not yet implemented")
```

- [ ] **Step 4: Create `cold_email/workers/logistics.py`**

```python
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    name="cold_email.workers.logistics.logistics_task",
)
def logistics_task(self, lead_id: str) -> dict:
    """
    Triggered by FastAPI POST /leads/{id}/approve.
    Steps (to be implemented):
      1. Fetch lead + most recent draft from DB
      2. Upsert lead into Instantly (name, email, company)
      3. Add lead to campaign with draft as first email
      4. Update lead.status = 'sent'
    """
    raise NotImplementedError("Logistics worker not yet implemented")
```

- [ ] **Step 5: Verify all four tasks are registered**

```bash
uv run python -c "
from cold_email.celery_app import app
app.loader.import_default_modules()
tasks = sorted(k for k in app.tasks if not k.startswith('celery.'))
for t in tasks:
    print(t)
"
```

Expected output (4 lines):
```
cold_email.workers.discovery.discovery_task
cold_email.workers.drafting.drafting_task
cold_email.workers.logistics.logistics_task
cold_email.workers.research.research_task
```

- [ ] **Step 6: Commit**

```bash
git add cold_email/workers/
git commit -m "chore: worker stubs with Celery task decorators"
```

---

### Task 9: FastAPI dashboard

**Files:**
- Create: `cold_email/api/main.py`
- Create: `cold_email/api/routes/dashboard.py`
- Create: `cold_email/templates/dashboard.html`

- [ ] **Step 1: Create `cold_email/api/main.py`**

```python
import pathlib
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from cold_email.api.routes import dashboard

TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"

app = FastAPI(title="Cold Email Dashboard")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.include_router(dashboard.router)
```

- [ ] **Step 2: Create `cold_email/api/routes/dashboard.py`**

```python
import pathlib
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from cold_email.database import get_async_session, Lead, Draft

TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_index(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(
        select(Lead)
        .where(Lead.status == "drafted")
        .options(selectinload(Lead.drafts))
        .order_by(Lead.created_at.desc())
    )
    leads = result.scalars().all()

    rows = []
    for lead in leads:
        # Pick the highest-version draft for display
        draft = max(lead.drafts, key=lambda d: d.version, default=None)
        if draft:
            rows.append({"lead": lead, "draft": draft})

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "rows": rows},
    )


@router.post("/leads/{lead_id}/approve")
async def approve_lead(
    lead_id: str,
    session: AsyncSession = Depends(get_async_session),
):
    lead = await session.get(Lead, lead_id)
    if lead:
        lead.status = "approved"
        await session.commit()
        from cold_email.workers.logistics import logistics_task
        logistics_task.delay(lead_id)
    return RedirectResponse(url="/", status_code=303)


@router.post("/leads/{lead_id}/reject")
async def reject_lead(
    lead_id: str,
    notes: str = Form(default=""),
    session: AsyncSession = Depends(get_async_session),
):
    lead = await session.get(Lead, lead_id)
    if lead:
        lead.status = "rejected"
        lead.error_msg = notes or None
        await session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/leads/{lead_id}/regenerate")
async def regenerate_lead(
    lead_id: str,
    session: AsyncSession = Depends(get_async_session),
):
    lead = await session.get(Lead, lead_id)
    if lead:
        # Step back to 'researched' so drafting_task re-runs from scratch
        lead.status = "researched"
        await session.commit()
        from cold_email.workers.drafting import drafting_task
        drafting_task.delay(lead_id)
    return RedirectResponse(url="/", status_code=303)
```

- [ ] **Step 3: Create `cold_email/templates/dashboard.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cold Email Dashboard</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 1100px;
            margin: 2rem auto;
            padding: 0 1.5rem;
            color: #1a202c;
            background: #f7fafc;
        }
        h1 { font-size: 1.5rem; margin-bottom: 1.5rem; }
        table {
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        th {
            background: #edf2f7;
            padding: 0.75rem 1rem;
            text-align: left;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #4a5568;
        }
        td {
            padding: 1rem;
            border-top: 1px solid #e2e8f0;
            vertical-align: top;
        }
        .company { font-weight: 600; }
        .meta { font-size: 0.8rem; color: #718096; margin-top: 0.2rem; }
        .subject { font-weight: 600; font-size: 0.9rem; margin-bottom: 0.4rem; }
        .body-text {
            font-size: 0.85rem;
            color: #4a5568;
            white-space: pre-wrap;
            line-height: 1.5;
            max-width: 380px;
        }
        .actions { display: flex; gap: 0.5rem; }
        form { margin: 0; }
        button {
            padding: 0.4rem 0.9rem;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 0.85rem;
            font-weight: 500;
        }
        .approve { background: #48bb78; color: white; }
        .approve:hover { background: #38a169; }
        .reject { background: #fc8181; color: white; }
        .reject:hover { background: #f56565; }
        .regenerate { background: #bee3f8; color: #2b6cb0; }
        .regenerate:hover { background: #90cdf4; }
        .empty {
            text-align: center;
            padding: 4rem;
            color: #a0aec0;
            font-size: 0.95rem;
            background: white;
            border-radius: 8px;
        }
    </style>
</head>
<body>
    <h1>Cold Email Dashboard</h1>
    {% if rows %}
    <table>
        <thead>
            <tr>
                <th>Company</th>
                <th>Founder</th>
                <th>Draft</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {% for row in rows %}
            <tr>
                <td>
                    <div class="company">{{ row.lead.company_name }}</div>
                    <div class="meta">
                        {{ row.lead.funding_stage or "—" }}
                        {% if row.lead.headcount %} · {{ row.lead.headcount }} people{% endif %}
                    </div>
                </td>
                <td>
                    <div>{{ row.lead.founder_name or "—" }}</div>
                    <div class="meta">{{ row.lead.founder_email or "" }}</div>
                </td>
                <td>
                    <div class="subject">{{ row.draft.subject_line }}</div>
                    <div class="body-text">{{ row.draft.body }}</div>
                </td>
                <td>
                    <div class="actions">
                        <form method="post" action="/leads/{{ row.lead.id }}/approve">
                            <button type="submit" class="approve">Approve</button>
                        </form>
                        <form method="post" action="/leads/{{ row.lead.id }}/reject">
                            <button type="submit" class="reject">Reject</button>
                        </form>
                        <form method="post" action="/leads/{{ row.lead.id }}/regenerate">
                            <button type="submit" class="regenerate">Regenerate</button>
                        </form>
                    </div>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div class="empty">No leads awaiting review. Run the discovery task to get started.</div>
    {% endif %}
</body>
</html>
```

- [ ] **Step 4: Verify FastAPI app starts**

```bash
uv run uvicorn cold_email.api.main:app --reload --port 8000
```

Open `http://localhost:8000`. Expected: dashboard renders with "No leads awaiting review."

- [ ] **Step 5: Commit**

```bash
git add cold_email/api/ cold_email/templates/
git commit -m "chore: FastAPI HITL dashboard with approve/reject/regenerate"
```

---

### Task 10: Test scaffolding

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_discovery.py`
- Create: `tests/test_research.py`
- Create: `tests/test_drafting.py`
- Create: `tests/test_logistics.py`

- [ ] **Step 1: Create test database**

Tests use a separate Postgres database so they don't pollute `cold_email`. Create it once:

```bash
psql postgresql://cold_email:secret@localhost:5432 -c "CREATE DATABASE cold_email_test;"
```

- [ ] **Step 2: Create `tests/conftest.py`**

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from cold_email.database import Base
from cold_email.config import settings

TEST_DB_URL = settings.database_url.replace("/cold_email", "/cold_email_test")


@pytest_asyncio.fixture(scope="function")
async def async_session() -> AsyncSession:
    """
    Creates all tables fresh for each test, yields a session, then drops everything.
    Requires: docker compose up -d and cold_email_test database to exist.
    """
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
```

- [ ] **Step 3: Create `tests/test_discovery.py`**

```python
import pytest
from cold_email.workers.discovery import discovery_task


def test_discovery_task_not_yet_implemented():
    """Placeholder — replace with real tests when discovery worker is implemented."""
    with pytest.raises(NotImplementedError):
        discovery_task.apply().get(propagate=True)
```

- [ ] **Step 4: Create `tests/test_research.py`**

```python
import pytest
from cold_email.workers.research import research_task

FAKE_UUID = "00000000-0000-0000-0000-000000000000"


def test_research_task_not_yet_implemented():
    """Placeholder — replace with real tests when research worker is implemented."""
    with pytest.raises(NotImplementedError):
        research_task.apply(args=[FAKE_UUID]).get(propagate=True)
```

- [ ] **Step 5: Create `tests/test_drafting.py`**

```python
import pytest
from cold_email.workers.drafting import drafting_task

FAKE_UUID = "00000000-0000-0000-0000-000000000000"


def test_drafting_task_not_yet_implemented():
    """Placeholder — replace with real tests when drafting worker is implemented."""
    with pytest.raises(NotImplementedError):
        drafting_task.apply(args=[FAKE_UUID]).get(propagate=True)
```

- [ ] **Step 6: Create `tests/test_logistics.py`**

```python
import pytest
from cold_email.workers.logistics import logistics_task

FAKE_UUID = "00000000-0000-0000-0000-000000000000"


def test_logistics_task_not_yet_implemented():
    """Placeholder — replace with real tests when logistics worker is implemented."""
    with pytest.raises(NotImplementedError):
        logistics_task.apply(args=[FAKE_UUID]).get(propagate=True)
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/ -v
```

Expected: 4 tests pass.

- [ ] **Step 8: Commit**

```bash
git add tests/
git commit -m "chore: test scaffolding with placeholder tests for each worker"
```

---

## End State Checklist

After all tasks, these should all pass:

```bash
# Infrastructure
docker compose ps                          # redis + postgres Up

# Dependencies
uv sync                                    # no errors

# Migration
psql postgresql://cold_email:secret@localhost:5432/cold_email -c "\dt"
# → leads, research, drafts

# Config
uv run python -c "from cold_email.config import settings; print(settings.sender_name)"
# → Liyu Xiao

# Task registration
uv run python -c "
from cold_email.celery_app import app
app.loader.import_default_modules()
print(sorted(k for k in app.tasks if not k.startswith('celery.')))
"
# → 4 worker task names

# Dashboard
uv run uvicorn cold_email.api.main:app --reload --port 8000
# → http://localhost:8000 renders empty dashboard

# Tests
uv run pytest tests/ -v
# → 4 passed
```
