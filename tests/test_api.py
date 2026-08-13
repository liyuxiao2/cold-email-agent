from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from cold_email.api.main import app
from cold_email.database import Lead, get_async_session


@pytest.fixture
def mock_session():
    session = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_health_check():
    mock_db = AsyncMock()
    mock_db.execute.return_value = MagicMock()
    app.dependency_overrides[get_async_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/health")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "connected"


@pytest.mark.asyncio
async def test_pipeline_stats():
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [("drafted", 5), ("found", 10), ("sent", 2)]
    mock_db.execute.return_value = mock_result
    app.dependency_overrides[get_async_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/pipeline/stats")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert data["drafted"] == 5
    assert data["found"] == 10
    assert data["sent"] == 2
    assert data["total"] == 17


@pytest.mark.asyncio
async def test_list_leads():
    mock_db = AsyncMock()

    mock_lead = MagicMock(spec=Lead)
    mock_lead.id = "00000000-0000-0000-0000-000000000001"
    mock_lead.company_name = "Acme Corp"
    mock_lead.founder_name = "Alice"
    mock_lead.founder_email = "alice@acme.com"
    mock_lead.company_url = "https://acme.com"
    mock_lead.linkedin_url = None
    mock_lead.funding_stage = "Seed"
    mock_lead.headcount = 10
    mock_lead.status = "drafted"
    mock_lead.error_msg = None
    mock_lead.created_at = None
    mock_lead.updated_at = None
    mock_lead.drafts = []
    mock_lead.research = []

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_lead]
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars

    mock_count_result = MagicMock()
    mock_count_result.scalar_one.return_value = 1

    mock_db.execute.side_effect = [mock_result, mock_count_result]
    app.dependency_overrides[get_async_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/leads?limit=10")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["company_name"] == "Acme Corp"


@pytest.mark.asyncio
async def test_trigger_discovery():
    with patch("cold_email.workers.discovery.discovery.discovery_task.delay") as mock_delay:
        mock_delay.return_value.id = "mock-task-id-123"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/pipeline/discovery")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["task_id"] == "mock-task-id-123"


@pytest.mark.asyncio
async def test_trigger_drafting():
    with patch("cold_email.workers.drafting.drafting.drafting_task.delay") as mock_delay:
        mock_delay.return_value.id = "mock-draft-task-456"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/pipeline/drafting")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["task_id"] == "mock-draft-task-456"


@pytest.mark.asyncio
async def test_trigger_research_requeues_found_leads():
    """Requeues only 'found' (orphaned, never-researched) leads. Terminally
    'failed' leads are recovered separately via the dead-letter queue."""
    found_lead = MagicMock(spec=Lead)
    found_lead.id = "00000000-0000-0000-0000-000000000001"
    found_lead.status = "found"

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [found_lead]
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result
    app.dependency_overrides[get_async_session] = lambda: mock_db

    with patch("cold_email.workers.research.research.research_task.delay") as mock_delay:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/pipeline/research")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["requeued"] == 1
    assert mock_delay.call_count == 1


@pytest.mark.asyncio
async def test_approve_lead():
    mock_db = AsyncMock()
    mock_lead = MagicMock(spec=Lead)
    mock_lead.id = "00000000-0000-0000-0000-000000000001"
    mock_lead.status = "drafted"
    mock_db.get.return_value = mock_lead
    app.dependency_overrides[get_async_session] = lambda: mock_db

    with patch("cold_email.workers.logistics.logistics.logistics_task.delay") as mock_delay:
        mock_delay.return_value.id = "logistics-task-789"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(f"/api/leads/{mock_lead.id}/approve")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["status"] == "approved"
    assert mock_lead.status == "approved"


@pytest.mark.asyncio
async def test_draft_review_queue_returns_newest_draft():
    """After a regenerate, the review queue must show the NEWEST draft, even when
    every draft row shares version=1 (version is vestigial). Selection is by
    created_at, consistent with the pending_sends view used for sending."""
    from datetime import datetime, timezone

    def _draft(body, gmail_id, when):
        d = MagicMock()
        d.id = gmail_id
        d.subject_line = "s"
        d.body = body
        d.version = 1  # every draft is v1 — the bug's precondition
        d.gmail_draft_id = gmail_id
        d.created_at = when
        return d

    old = _draft("OLD freeform body", "gmail-old", datetime(2026, 8, 13, 0, 58, tzinfo=timezone.utc))
    new = _draft("Hi Kenny, new template", "gmail-new", datetime(2026, 8, 13, 1, 50, tzinfo=timezone.utc))

    lead = MagicMock(spec=Lead)
    lead.id = "00000000-0000-0000-0000-00000000000a"
    lead.company_name = "Turo"
    lead.founder_name = "Kenny"
    lead.founder_email = "k@turo.com"
    lead.company_url = "https://turo.com"
    lead.linkedin_url = None
    lead.funding_stage = None
    lead.headcount = None
    lead.status = "drafted"
    lead.created_at = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
    lead.drafts = [old, new]  # oldest FIRST — trips max(key=version) tie
    lead.research = []

    mock_db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [lead]
    mock_db.execute.return_value = result
    app.dependency_overrides[get_async_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/leads/drafts")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    draft = response.json()[0]["draft"]
    assert draft["gmail_draft_id"] == "gmail-new"
    assert draft["body"] == "Hi Kenny, new template"
