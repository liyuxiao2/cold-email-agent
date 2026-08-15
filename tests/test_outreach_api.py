import pytest


@pytest.mark.asyncio
async def test_creates_queued_rows_with_a_selected_contact(
    user_client, pool_fixture, async_session
):
    ids = [c["id"] for c in (await user_client.get("/api/companies")).json()["items"]]
    body = (await user_client.post("/api/outreach", json={"company_ids": ids[:1]})).json()

    assert len(body["created"]) == 1
    assert body["created"][0]["contact_id"] is not None

    from sqlalchemy import select

    from cold_email.database import Outreach

    outreach = (await async_session.execute(select(Outreach))).scalar_one()
    assert outreach.status == "queued"


@pytest.mark.asyncio
async def test_partial_success_when_one_company_is_exhausted(
    user_client, pool_fixture, exhausted_company, async_session
):
    """3 selected, 1 exhausted → 2 created, 1 skipped. A 400 with nothing
    created would be hostile: the pool changed under the user.

    NOTE: fetches ids directly via async_session, not via GET /api/companies
    — the pool endpoint (correctly) never shows an exhausted company, so
    fetching "all company ids" has to bypass it to exercise the exhaustion
    path at all.
    """
    from sqlalchemy import select

    from cold_email.database import Company

    all_ids = [str(c.id) for c in (await async_session.execute(select(Company))).scalars()]
    body = (await user_client.post("/api/outreach", json={"company_ids": all_ids})).json()

    assert any(s["reason"] == "no_available_contact" for s in body["skipped"])
    assert len(body["created"]) >= 1


@pytest.mark.asyncio
async def test_reselecting_a_company_is_skipped_not_duplicated(user_client, pool_fixture):
    ids = [c["id"] for c in (await user_client.get("/api/companies")).json()["items"]][:1]
    await user_client.post("/api/outreach", json={"company_ids": ids})

    body = (await user_client.post("/api/outreach", json={"company_ids": ids})).json()
    assert body["created"] == []
    assert body["skipped"][0]["reason"] == "already_targeted"


@pytest.mark.asyncio
async def test_unresearched_companies_are_skipped(user_client, pool_fixture, async_session):
    from sqlalchemy import select

    from cold_email.database import Company

    found = (
        await async_session.execute(select(Company).where(Company.company_name == "FoundCo"))
    ).scalar_one()

    body = (await user_client.post("/api/outreach", json={"company_ids": [str(found.id)]})).json()
    assert body["skipped"][0]["reason"] == "not_researched"


@pytest.mark.asyncio
async def test_over_quota_creates_the_allowed_subset(
    user_client, pool_fixture, async_session, set_quota
):
    """Fetches all company ids directly via async_session, not via GET
    /api/companies: the pool only ever shows ONE available company
    (ResearchedCo) in pool_fixture, which can't demonstrate "requested >
    allowed" on its own. Submitting every company id (including the
    not-researched/no-contact ones the pool already hides) exercises the
    quota clamp regardless of whether those rows would also have failed for
    a different reason — the quota check runs first."""
    from sqlalchemy import select

    from cold_email.database import Company

    await set_quota(1)
    all_ids = [str(c.id) for c in (await async_session.execute(select(Company))).scalars()]

    body = (await user_client.post("/api/outreach", json={"company_ids": all_ids})).json()
    assert len(body["created"]) == 1
    assert any(s["reason"] == "quota_exceeded" for s in body["skipped"])


@pytest.mark.asyncio
async def test_one_drafting_task_is_dispatched_per_batch(user_client, pool_fixture, monkeypatch):
    """The task sweeps all of the user's queued rows, so per-company dispatch
    would be redundant work."""
    dispatched = []
    monkeypatch.setattr(
        "cold_email.api.routes.outreach.drafting_task",
        type("T", (), {"delay": staticmethod(lambda uid: dispatched.append(uid))}),
    )

    ids = [c["id"] for c in (await user_client.get("/api/companies")).json()["items"]]
    await user_client.post("/api/outreach", json={"company_ids": ids})
    assert len(dispatched) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject", "regenerate"])
async def test_another_users_outreach_is_404(user_client, other_user_outreach, action):
    """404, not 403: a 403 confirms the id exists, making the endpoint an
    existence oracle."""
    assert (
        await user_client.post(f"/api/outreach/{other_user_outreach.id}/{action}")
    ).status_code == 404


@pytest.mark.asyncio
async def test_quota_endpoint_reports_usage(user_client, pool_fixture):
    body = (await user_client.get("/api/quota")).json()
    assert set(body) >= {"used", "limit", "period_end"}


@pytest.mark.asyncio
async def test_llm_key_is_never_returned(user_client, monkeypatch):
    monkeypatch.setattr(
        "cold_email.api.routes.outreach.validate_llm_key", lambda provider, key: True
    )
    await user_client.put("/api/llm-key", json={"provider": "groq", "api_key": "gsk_secret123"})

    body = (await user_client.get("/api/llm-key")).json()
    assert "api_key" not in body
    assert body == {"provider": "groq", "configured": True, "last4": "t123"}


@pytest.mark.asyncio
async def test_invalid_llm_key_is_422_and_stores_nothing(user_client, monkeypatch, async_session):
    """Storing an invalid key means the user's next 40 drafts fail one at a time
    in a Celery worker — a DLQ full of auth errors instead of form validation."""
    monkeypatch.setattr(
        "cold_email.api.routes.outreach.validate_llm_key", lambda provider, key: False
    )
    response = await user_client.put("/api/llm-key", json={"provider": "groq", "api_key": "bad"})
    assert response.status_code == 422

    from sqlalchemy import select

    from cold_email.database import User

    user = (
        await async_session.execute(select(User).where(User.email == "user@example.com"))
    ).scalar_one()
    assert user.llm_api_key_enc is None
