from datetime import UTC, datetime, timedelta

import pytest


@pytest.mark.asyncio
async def test_dispatches_null_and_past_schedules(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    dispatched = []
    monkeypatch.setattr(
        "cold_email.workers.logistics.logistics.logistics_task",
        type("T", (), {"delay": staticmethod(lambda oid: dispatched.append(oid))}),
    )

    now = datetime.now(UTC)
    await approved_outreach_factory(scheduled_send_at=None)
    await approved_outreach_factory(scheduled_send_at=now - timedelta(minutes=5))
    await approved_outreach_factory(scheduled_send_at=now + timedelta(hours=3))

    from cold_email.workers.logistics.logistics import send_due_task

    result = send_due_task()
    assert result["dispatched"] == 2
    assert len(dispatched) == 2


@pytest.mark.asyncio
async def test_one_failed_dispatch_does_not_strand_the_rest_of_the_batch(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    """A Redis hiccup on one row's .delay() must not abort dispatch for the
    other claimed rows, and the row whose dispatch failed must be released
    back to 'approved' immediately -- nothing was sent for it, so there is no
    reason to make it wait 30-90 minutes for reap_stuck_sends to dead-letter
    it as 'outcome unknown'."""
    from cold_email.database import OUTREACH_APPROVED, OUTREACH_SENDING

    outreach_a = await approved_outreach_factory(scheduled_send_at=None)
    outreach_b = await approved_outreach_factory(scheduled_send_at=None)
    outreach_c = await approved_outreach_factory(scheduled_send_at=None)

    dispatched = []

    def flaky_delay(oid):
        if oid == str(outreach_b.id):
            raise ConnectionError("broker unreachable")
        dispatched.append(oid)

    monkeypatch.setattr(
        "cold_email.workers.logistics.logistics.logistics_task",
        type("T", (), {"delay": staticmethod(flaky_delay)}),
    )

    from cold_email.workers.logistics.logistics import send_due_task

    result = send_due_task()

    assert result["dispatched"] == 2
    assert str(outreach_a.id) in dispatched
    assert str(outreach_c.id) in dispatched
    assert str(outreach_b.id) not in dispatched

    await async_session.refresh(outreach_a)
    await async_session.refresh(outreach_b)
    await async_session.refresh(outreach_c)
    assert outreach_a.status == OUTREACH_SENDING
    assert outreach_c.status == OUTREACH_SENDING
    # Released back to 'approved', not stranded at 'sending'.
    assert outreach_b.status == OUTREACH_APPROVED


@pytest.mark.asyncio
async def test_overlapping_scans_dispatch_each_row_exactly_once(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    """THE test for this stack. Celery guarantees at-least-once task delivery, so
    a scanner over rows that only leave the set on success will eventually
    dispatch the same row twice — and a cold email sent twice to a founder
    cannot be undone.

    The claim UPDATE is what prevents it: the second scan's UPDATE matches
    nothing.
    """
    dispatched = []
    monkeypatch.setattr(
        "cold_email.workers.logistics.logistics.logistics_task",
        type("T", (), {"delay": staticmethod(lambda oid: dispatched.append(oid))}),
    )

    await approved_outreach_factory(scheduled_send_at=None)

    from cold_email.workers.logistics.logistics import send_due_task

    send_due_task()
    send_due_task()

    assert len(dispatched) == 1
    assert len(set(dispatched)) == 1


@pytest.mark.asyncio
async def test_claimed_rows_move_to_sending(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    monkeypatch.setattr(
        "cold_email.workers.logistics.logistics.logistics_task",
        type("T", (), {"delay": staticmethod(lambda oid: None)}),
    )
    outreach = await approved_outreach_factory(scheduled_send_at=None)

    from cold_email.database import OUTREACH_SENDING
    from cold_email.workers.logistics.logistics import send_due_task

    send_due_task()
    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_SENDING


@pytest.mark.asyncio
async def test_a_row_already_sending_is_not_redispatched(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    dispatched = []
    monkeypatch.setattr(
        "cold_email.workers.logistics.logistics.logistics_task",
        type("T", (), {"delay": staticmethod(lambda oid: dispatched.append(oid))}),
    )

    from cold_email.database import OUTREACH_SENDING

    outreach = await approved_outreach_factory(scheduled_send_at=None)
    outreach.status = OUTREACH_SENDING
    await async_session.commit()

    from cold_email.workers.logistics.logistics import send_due_task

    send_due_task()
    assert dispatched == []


@pytest.mark.asyncio
async def test_logistics_task_is_a_noop_when_the_row_is_not_sending(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    """The second guard: a duplicate Celery delivery must not send again."""
    sent = []
    monkeypatch.setattr(
        "cold_email.workers.logistics.logistics.send_draft",
        lambda creds, draft_id: sent.append(draft_id) or "msg-1",
    )

    from cold_email.database import OUTREACH_SENT

    outreach = await approved_outreach_factory(scheduled_send_at=None)
    outreach.status = OUTREACH_SENT
    await async_session.commit()

    from cold_email.workers.logistics.logistics import logistics_task

    logistics_task(str(outreach.id))
    assert sent == []


@pytest.mark.asyncio
async def test_stuck_sending_rows_are_dead_lettered_not_retried(
    async_session, approved_outreach_factory, sync_session_for
):
    """Automatically retrying a send whose outcome is unknown is precisely how a
    double-send happens."""
    from cold_email.database import OUTREACH_SENDING, DeadLetter

    outreach = await approved_outreach_factory(scheduled_send_at=None)
    outreach.status = OUTREACH_SENDING
    outreach.updated_at = datetime.now(UTC) - timedelta(hours=2)
    await async_session.commit()

    from cold_email.workers.logistics.logistics import reap_stuck_sends

    assert reap_stuck_sends()["reaped"] == 1

    from sqlalchemy import select

    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert dl.stage == "logistics"
    assert "unknown" in dl.error_msg.lower()


@pytest.mark.asyncio
async def test_recently_claimed_rows_are_not_reaped(
    async_session, approved_outreach_factory, sync_session_for
):
    from cold_email.database import OUTREACH_SENDING

    outreach = await approved_outreach_factory(scheduled_send_at=None)
    outreach.status = OUTREACH_SENDING
    await async_session.commit()

    from cold_email.workers.logistics.logistics import reap_stuck_sends

    assert reap_stuck_sends()["reaped"] == 0


@pytest.mark.asyncio
async def test_uses_the_owning_users_credentials(
    async_session, two_users_approved, sync_session_for, monkeypatch
):
    used = []
    monkeypatch.setattr(
        "cold_email.workers.logistics.logistics.send_draft",
        lambda creds, draft_id: used.append(creds.sender_email) or "msg-1",
    )

    from cold_email.workers.logistics.logistics import logistics_task

    logistics_task(str(two_users_approved["outreach_a"].id))
    assert used == ["a@example.com"]


@pytest.mark.asyncio
async def test_a_deleted_gmail_draft_fails_only_that_row(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    """gmail_draft_id points at a resource the user can delete by hand between
    approving and the scheduled send. It must never abort the scan."""
    from googleapiclient.errors import HttpError

    def boom(creds, draft_id):
        # HttpError's own constructor reads resp.reason (via _get_reason), so
        # the fake response needs one too or building the exception itself
        # raises AttributeError before logistics_task ever sees an HttpError.
        raise HttpError(
            resp=type("R", (), {"status": 404, "reason": "Not Found"})(), content=b"not found"
        )

    monkeypatch.setattr("cold_email.workers.logistics.logistics.send_draft", boom)

    from cold_email.database import OUTREACH_FAILED, OUTREACH_SENDING

    outreach = await approved_outreach_factory(scheduled_send_at=None)
    outreach.status = OUTREACH_SENDING
    await async_session.commit()

    from cold_email.workers.logistics.logistics import logistics_task

    logistics_task(str(outreach.id))
    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_FAILED
