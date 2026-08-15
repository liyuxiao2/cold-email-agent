from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from cold_email.api.main import app
from cold_email.database import (
    OUTREACH_DRAFTED,
    RESEARCH_FOUND,
    Company,
    CompanyContact,
    DeadLetter,
    Draft,
    Outreach,
    User,
    get_async_session,
)


@pytest.fixture
def mock_session():
    session = AsyncMock()
    return session


@pytest_asyncio.fixture
async def own_outreach(async_session, user_client):
    """A drafted outreach row owned by `user_client`'s own account, with a
    company, an eligible contact, and a draft — enough to exercise every
    field the serializer emits."""
    user = (
        await async_session.execute(select(User).where(User.email == "user@example.com"))
    ).scalar_one()

    company = Company(
        company_name="Acme Corp",
        company_url="https://acme.com",
        founder_name="Alice",
        funding_stage="Seed",
        headcount=10,
    )
    async_session.add(company)
    await async_session.commit()

    contact = CompanyContact(
        company_id=company.id,
        email="alice@acme.com",
        first_name="Alice",
        position="Founder",
        confidence=90,
        is_founder=True,
        eligible=True,
    )
    async_session.add(contact)
    await async_session.commit()

    outreach = Outreach(
        user_id=user.id,
        company_id=company.id,
        contact_id=contact.id,
        status=OUTREACH_DRAFTED,
    )
    async_session.add(outreach)
    await async_session.commit()

    # Explicit (past) created_at rather than the server default: this fixture
    # is reused by a test that adds a second, newer draft and asserts the
    # newest wins — that ordering must not depend on wall-clock "now".
    draft = Draft(
        outreach_id=outreach.id,
        subject_line="Hi Alice",
        body="Body text",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    async_session.add(draft)
    await async_session.commit()

    await async_session.refresh(outreach)
    return outreach


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
async def test_stats_reports_both_levels(async_session, user_client):
    body = (await user_client.get("/api/pipeline/stats")).json()
    assert "companies" in body and "outreach" in body
    assert set(body["companies"]) >= {"found", "researched", "failed"}
    assert set(body["outreach"]) >= {
        "queued",
        "drafted",
        "approved",
        "sent",
        "rejected",
        "failed",
    }


@pytest.mark.asyncio
async def test_stats_outreach_is_scoped_to_caller(async_session, user_client, other_user_outreach):
    """The `outreach` half of /api/pipeline/stats must not count another
    user's rows — the same tenancy boundary as every other outreach query."""
    body = (await user_client.get("/api/pipeline/stats")).json()
    assert body["outreach"]["drafted"] == 0


@pytest.mark.asyncio
async def test_list_outreach_includes_company_and_contact(user_client, own_outreach):
    response = await user_client.get("/api/outreach")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["company"]["company_name"] == "Acme Corp"
    assert item["contact"]["email"] == "alice@acme.com"
    assert item["contact"]["position"] == "Founder"


@pytest.mark.asyncio
async def test_drafts_queue_returns_only_the_callers_rows(
    async_session, user_client, other_user_outreach
):
    """Tenancy isolation. Invisible in single-user manual testing and
    catastrophic in production."""
    body = (await user_client.get("/api/outreach/drafts")).json()
    assert body == []


@pytest.mark.asyncio
async def test_drafts_queue_includes_the_callers_own_row(user_client, own_outreach):
    body = (await user_client.get("/api/outreach/drafts")).json()
    assert len(body) == 1
    assert body[0]["outreach_id"] == str(own_outreach.id)
    assert body[0]["company"]["company_name"] == "Acme Corp"
    assert body[0]["contact"]["first_name"] == "Alice"
    assert body[0]["draft"]["subject_line"] == "Hi Alice"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject", "regenerate"])
async def test_cannot_mutate_another_users_outreach(
    async_session, user_client, other_user_outreach, action
):
    """404, not 403: a 403 confirms the id exists, turning an authorization
    check into an existence oracle."""
    response = await user_client.post(f"/api/outreach/{other_user_outreach.id}/{action}")
    assert response.status_code == 404

    await async_session.refresh(other_user_outreach)
    assert other_user_outreach.status == "drafted"  # unchanged


@pytest.mark.asyncio
async def test_approve_own_outreach(async_session, user_client, own_outreach):
    """Approve sets state only. Dispatch is send_due_task's job (the Beat
    scanner claims 'approved' rows every 5 minutes), so no logistics_task
    call happens here -- see tests/test_scheduling_api.py for the scheduling
    semantics this endpoint now carries."""
    response = await user_client.post(f"/api/outreach/{own_outreach.id}/approve")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["status"] == "approved"
    assert response.json()["scheduled_send_at"] is None

    await async_session.refresh(own_outreach)
    assert own_outreach.status == "approved"


@pytest.mark.asyncio
async def test_reject_own_outreach_saves_notes(async_session, user_client, own_outreach):
    response = await user_client.post(
        f"/api/outreach/{own_outreach.id}/reject", json={"notes": "wrong fit"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    await async_session.refresh(own_outreach)
    assert own_outreach.status == "rejected"
    assert own_outreach.error_msg == "wrong fit"


@pytest.mark.asyncio
async def test_regenerate_own_outreach_resets_to_queued(async_session, user_client, own_outreach):
    with patch("cold_email.workers.drafting.drafting.drafting_task.delay") as mock_delay:
        mock_delay.return_value.id = "drafting-task-456"
        response = await user_client.post(f"/api/outreach/{own_outreach.id}/regenerate")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"

    await async_session.refresh(own_outreach)
    assert own_outreach.status == "queued"


@pytest.mark.asyncio
async def test_trigger_discovery(admin_client):
    with patch("cold_email.workers.discovery.discovery.discovery_task.delay") as mock_delay:
        mock_delay.return_value.id = "mock-task-id-123"
        response = await admin_client.post("/api/pipeline/discovery")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["task_id"] == "mock-task-id-123"


@pytest.mark.asyncio
async def test_trigger_drafting(admin_client):
    """Drafting itself is dispatched by POST /api/outreach now; this admin
    route triggers the same hourly recovery sweep Beat runs, for users whose
    queued rows have been sitting long enough to look like a lost dispatch."""
    with patch("cold_email.workers.drafting.drafting.drafting_recovery_task.delay") as mock_delay:
        mock_delay.return_value.id = "mock-draft-task-456"
        response = await admin_client.post("/api/pipeline/drafting")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["task_id"] == "mock-draft-task-456"


@pytest.mark.asyncio
async def test_trigger_research_requeues_found_companies(async_session, admin_client):
    """Requeues only 'found' (orphaned, never-researched) companies. Terminally
    'failed' companies are recovered separately via the dead-letter queue."""
    company = Company(company_name="Orphan Co", research_status=RESEARCH_FOUND)
    async_session.add(company)
    await async_session.commit()

    with patch("cold_email.workers.research.research.research_task.delay") as mock_delay:
        response = await admin_client.post("/api/pipeline/research")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["requeued"] == 1
    assert mock_delay.call_count == 1


@pytest.mark.asyncio
async def test_draft_review_queue_returns_newest_draft(async_session, user_client, own_outreach):
    """After a regenerate, the review queue must show the NEWEST draft, even when
    every draft row shares version=1 (version is vestigial). Selection is by
    created_at, consistent with the pending_sends view used for sending."""
    # own_outreach already carries one draft (created "now"); add an older one
    # with a later `created_at` to prove the newest — not the last-inserted —
    # wins.
    newer = Draft(
        outreach_id=own_outreach.id,
        subject_line="s",
        body="Hi Kenny, new template",
        version=1,
        gmail_draft_id="gmail-new",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    async_session.add(newer)
    await async_session.commit()

    response = await user_client.get("/api/outreach/drafts")

    assert response.status_code == 200
    draft = response.json()[0]["draft"]
    assert draft["gmail_draft_id"] == "gmail-new"
    assert draft["body"] == "Hi Kenny, new template"


@pytest.mark.asyncio
async def test_dlq_hides_another_users_drafting_failure(
    async_session, user_client, other_user_outreach
):
    """GET /api/dlq is the only DLQ route with no admin gate, so an
    outreach-anchored (drafting/logistics) dead-letter row — one user's
    problem — must not leak another user's outreach_id, company name, and
    error_msg. A company-anchored (research) row is a global fact and must be
    visible to both, modeled on the existing other_user_outreach pattern."""
    other_drafting_failure = DeadLetter(
        outreach_id=other_user_outreach.id,
        task_name="drafting_task",
        stage="drafting",
        error_msg="other user's secret failure",
    )
    research_company = Company(company_name="ResearchFailCo", research_status="failed")
    async_session.add_all([other_drafting_failure, research_company])
    await async_session.commit()
    research_failure = DeadLetter(
        company_id=research_company.id,
        task_name="research_task",
        stage="research",
        error_msg="no eligible contact",
    )
    async_session.add(research_failure)
    await async_session.commit()

    body = (await user_client.get("/api/dlq")).json()

    stages = {item["stage"] for item in body["items"]}
    assert "drafting" not in stages
    assert "research" in stages
    assert body["count"] == len(body["items"])
    for item in body["items"]:
        assert item["error_msg"] != "other user's secret failure"


@pytest.mark.asyncio
async def test_dlq_retry_drafting_stage_resets_and_redispatches(
    async_session, admin_client, own_outreach
):
    """End-to-end coverage for POST /api/dlq/retry?stage=drafting: dlq.py is
    the only caller of drafting_task.delay(user_id) outside outreach.py, and
    it was already wrong once (it used to dispatch with the outreach id
    instead of the row's owning user_id, since drafting_task became
    per-user)."""
    own_outreach.status = "failed"
    own_outreach.error_msg = "Model returned an empty draft"
    dead_letter = DeadLetter(
        outreach_id=own_outreach.id,
        task_name="cold_email.workers.drafting.drafting_task",
        stage="drafting",
        error_msg="Model returned an empty draft",
    )
    async_session.add(dead_letter)
    await async_session.commit()

    with patch("cold_email.workers.drafting.drafting.drafting_task.delay") as mock_delay:
        response = await admin_client.post("/api/dlq/retry", params={"stage": "drafting"})

    assert response.status_code == 200
    assert response.json() == {"success": True, "retried": 1}

    mock_delay.assert_called_once_with(str(own_outreach.user_id))

    await async_session.refresh(own_outreach)
    assert own_outreach.status == "queued"
    assert own_outreach.error_msg is None

    remaining = (await async_session.execute(select(DeadLetter))).scalars().all()
    assert remaining == []
