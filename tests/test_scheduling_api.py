from datetime import UTC, datetime, timedelta

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
