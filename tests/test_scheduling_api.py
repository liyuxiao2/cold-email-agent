from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

CADENCE = {
    "max_per_day": 2,
    "days": [0, 1, 2, 3, 4, 5, 6],
    "window_start": "09:00",
    "window_end": "17:00",
    "timezone": "America/Toronto",
}


@pytest.mark.asyncio
async def test_explicit_datetime_is_stored(user_client, drafted_outreach, async_session):
    when = datetime.now(UTC) + timedelta(days=3)
    response = await user_client.post(
        f"/api/outreach/{drafted_outreach.id}/approve",
        json={"scheduled_send_at": when.isoformat()},
    )
    assert response.status_code == 200

    await async_session.refresh(drafted_outreach)
    assert drafted_outreach.status == "approved"
    assert abs((drafted_outreach.scheduled_send_at - when).total_seconds()) < 1


@pytest.mark.asyncio
async def test_send_now_overrides_the_cadence(
    user_client, drafted_outreach, async_session, with_cadence
):
    await user_client.post(f"/api/outreach/{drafted_outreach.id}/approve", json={"send_now": True})
    await async_session.refresh(drafted_outreach)
    assert drafted_outreach.scheduled_send_at is None  # NULL = next tick


@pytest.mark.asyncio
async def test_empty_body_with_a_cadence_computes_a_slot(
    user_client, drafted_outreach, async_session, with_cadence
):
    await user_client.post(f"/api/outreach/{drafted_outreach.id}/approve")
    await async_session.refresh(drafted_outreach)
    assert drafted_outreach.scheduled_send_at is not None


@pytest.mark.asyncio
async def test_empty_body_without_a_cadence_sends_immediately(
    user_client, drafted_outreach, async_session
):
    await user_client.post(f"/api/outreach/{drafted_outreach.id}/approve")
    await async_session.refresh(drafted_outreach)
    assert drafted_outreach.scheduled_send_at is None


@pytest.mark.asyncio
async def test_a_past_timestamp_is_accepted(user_client, drafted_outreach):
    """Rejecting a timestamp that went stale while the user read the draft would
    be hostile; 'send immediately' is a reasonable reading."""
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    response = await user_client.post(
        f"/api/outreach/{drafted_outreach.id}/approve", json={"scheduled_send_at": past}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_beyond_the_horizon_is_422(user_client, drafted_outreach):
    far = (datetime.now(UTC) + timedelta(days=120)).isoformat()
    response = await user_client.post(
        f"/api/outreach/{drafted_outreach.id}/approve", json={"scheduled_send_at": far}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bulk_approve_spreads_slots_across_the_batch(
    user_client, three_drafted_outreach, async_session, with_cadence
):
    """Without this, approving 30 drafts produces 30 identical slots and cadence
    is unusable at exactly the volume that makes cadence necessary."""
    ids = [str(o.id) for o in three_drafted_outreach]
    response = await user_client.post("/api/outreach/bulk-approve", json={"outreach_ids": ids})
    assert response.status_code == 200

    for outreach in three_drafted_outreach:
        await async_session.refresh(outreach)

    slots = sorted(o.scheduled_send_at for o in three_drafted_outreach)
    assert len(set(slots)) == 3  # all distinct
    assert slots == sorted(slots)


@pytest.mark.asyncio
async def test_bulk_approve_without_a_cadence_leaves_every_slot_null(
    user_client, three_drafted_outreach, async_session
):
    ids = [str(o.id) for o in three_drafted_outreach]
    await user_client.post("/api/outreach/bulk-approve", json={"outreach_ids": ids})

    for outreach in three_drafted_outreach:
        await async_session.refresh(outreach)
        assert outreach.scheduled_send_at is None


@pytest.mark.asyncio
async def test_unsatisfiable_cadence_is_409(
    user_client, drafted_outreach, async_session, company_factory
):
    from sqlalchemy import select

    from cold_email.database import OUTREACH_APPROVED, Outreach, User

    user = (
        await async_session.execute(select(User).where(User.email == "user@example.com"))
    ).scalar_one()
    user.send_cadence = {
        "max_per_day": 1,
        "days": [6],
        "window_start": "09:00",
        "window_end": "10:00",
        "timezone": "America/Toronto",
    }
    await async_session.commit()

    # Fill the horizon so no slot remains: far more approved rows than the
    # ~13 Sundays inside the 90-day horizon holds. next_slot's fail-fast
    # check compares len(scheduled) to the horizon's total capacity, so the
    # exact dates these land on don't matter -- only the count does.
    base = datetime.now(UTC)
    for week in range(200):
        company = await company_factory()
        async_session.add(
            Outreach(
                user_id=user.id,
                company_id=company.id,
                status=OUTREACH_APPROVED,
                scheduled_send_at=base + timedelta(weeks=week),
            )
        )
    await async_session.commit()

    response = await user_client.post(f"/api/outreach/{drafted_outreach.id}/approve")
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_sent_rows_still_count_against_the_days_cap(
    user_client, drafted_outreach, async_session, company_factory
):
    """Fix 3: a row that already reached 'sent' must still count against its
    day's cadence budget. The exact bug this guards: cap 10/day, ten
    approvals go out over the morning and are all 'sent' by noon; if 'sent'
    rows dropped out of the day's count, a second batch of ten approved at
    noon would see an empty `existing` and land SIX more on the same local
    day -- sixteen sends against a cap of ten. Reproduced here at a smaller
    scale (cap 5): five already-'sent' rows fill today's budget, so a sixth
    approval must be pushed to a later day even though nothing is currently
    'approved' or 'sending'."""
    from sqlalchemy import select

    from cold_email.database import OUTREACH_SENT, Outreach, User

    user = (
        await async_session.execute(select(User).where(User.email == "user@example.com"))
    ).scalar_one()
    user.send_cadence = {
        "max_per_day": 5,
        "days": [0, 1, 2, 3, 4, 5, 6],
        "window_start": "00:00",
        "window_end": "23:55",
        "timezone": "America/Toronto",
    }
    await async_session.commit()

    now = datetime.now(UTC)
    for minutes in range(5):
        company = await company_factory()
        async_session.add(
            Outreach(
                user_id=user.id,
                company_id=company.id,
                status=OUTREACH_SENT,
                scheduled_send_at=now + timedelta(minutes=minutes),
            )
        )
    await async_session.commit()

    response = await user_client.post(f"/api/outreach/{drafted_outreach.id}/approve")
    assert response.status_code == 200

    await async_session.refresh(drafted_outreach)
    zone = ZoneInfo("America/Toronto")
    assert drafted_outreach.scheduled_send_at.astimezone(zone).date() > now.astimezone(zone).date()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"max_per_day": 0},
        {"max_per_day": 51},
        {"days": []},
        {"days": [9]},
        {"window_start": "18:00", "window_end": "09:00"},
        {"timezone": "Not/AZone"},
    ],
)
async def test_cadence_validation_rejects_bad_fields(user_client, override):
    response = await user_client.put("/api/cadence", json={**CADENCE, **override})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cadence_crud(user_client):
    assert (await user_client.get("/api/cadence")).json()["cadence"] is None

    await user_client.put("/api/cadence", json=CADENCE)
    assert (await user_client.get("/api/cadence")).json()["cadence"]["max_per_day"] == 2

    await user_client.delete("/api/cadence")
    assert (await user_client.get("/api/cadence")).json()["cadence"] is None


@pytest.mark.asyncio
async def test_unschedule_returns_the_row_to_drafted(
    user_client, drafted_outreach, async_session, with_cadence
):
    """drafted, not queued: the draft exists, and re-running the LLM would spend
    quota and produce copy the user never reviewed."""
    await user_client.post(f"/api/outreach/{drafted_outreach.id}/approve")
    await user_client.post(f"/api/outreach/{drafted_outreach.id}/unschedule")

    await async_session.refresh(drafted_outreach)
    assert drafted_outreach.status == "drafted"
    assert drafted_outreach.scheduled_send_at is None


@pytest.mark.asyncio
async def test_scheduled_queue_returns_only_the_callers_rows(user_client, other_user_scheduled):
    body = (await user_client.get("/api/outreach/scheduled")).json()
    assert body["items"] == []


@pytest.mark.asyncio
async def test_scheduled_queue_includes_the_callers_own_scheduled_row(
    user_client, drafted_outreach, with_cadence
):
    await user_client.post(f"/api/outreach/{drafted_outreach.id}/approve")
    body = (await user_client.get("/api/outreach/scheduled")).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["outreach_id"] == str(drafted_outreach.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "unschedule"])
async def test_another_users_row_is_404(user_client, other_user_outreach, action):
    assert (
        await user_client.post(f"/api/outreach/{other_user_outreach.id}/{action}")
    ).status_code == 404
