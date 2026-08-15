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
async def test_quota_is_gated_on_rows_created_not_request_position(
    user_client, async_session, pending_views, set_quota
):
    """The degenerate case the spec's own partial-success example hinges on:
    companies skipped for a NON-quota reason inside the first `allowed` slots
    must not burn a quota unit or mislabel a later, otherwise-fine company as
    "quota_exceeded" while quota sits unspent.

    3 already-targeted companies + 2 fresh eligible ones, quota allowed=2,
    already-targeted ones submitted first: gating on request position would
    let the 2 already-targeted companies consume both "allowed" slots, so
    both fresh companies (and the 3rd already-targeted one) would be
    wrongly reported quota_exceeded with 0 actually created. Gating on
    len(created) must instead create exactly 2 (the fresh ones) and report
    all 3 already-targeted companies with their real reason.
    """
    from sqlalchemy import select

    from cold_email.database import (
        RESEARCH_RESEARCHED,
        Company,
        CompanyContact,
        Outreach,
        User,
    )

    user = (
        await async_session.execute(select(User).where(User.email == "user@example.com"))
    ).scalar_one()

    already_targeted = []
    for i in range(3):
        company = Company(company_name=f"AlreadyCo{i}", research_status=RESEARCH_RESEARCHED)
        async_session.add(company)
        await async_session.commit()
        contact = CompanyContact(
            company_id=company.id,
            email=f"founder{i}@already.co",
            first_name="Fay",
            is_founder=True,
            eligible=True,
            confidence=90,
        )
        async_session.add(contact)
        async_session.add(Outreach(user_id=user.id, company_id=company.id, contact_id=None))
        await async_session.commit()
        already_targeted.append(str(company.id))

    fresh = []
    for i in range(2):
        company = Company(company_name=f"FreshCo{i}", research_status=RESEARCH_RESEARCHED)
        async_session.add(company)
        await async_session.commit()
        contact = CompanyContact(
            company_id=company.id,
            email=f"founder{i}@fresh.co",
            first_name="Fay",
            is_founder=True,
            eligible=True,
            confidence=90,
        )
        async_session.add(contact)
        await async_session.commit()
        fresh.append(str(company.id))

    # Quota usage counts EVERY outreach row created this period, including the
    # 3 already-targeted ones seeded above — so the limit must clear that
    # count before the 2 fresh companies have any room: allowed = limit - 3.
    await set_quota(5)

    body = (
        await user_client.post("/api/outreach", json={"company_ids": already_targeted + fresh})
    ).json()

    assert len(body["created"]) == 2
    assert {c["company_id"] for c in body["created"]} == set(fresh)

    assert len(body["skipped"]) == 3
    for skip in body["skipped"]:
        assert skip["reason"] == "already_targeted"
        assert skip["company_id"] in already_targeted


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
