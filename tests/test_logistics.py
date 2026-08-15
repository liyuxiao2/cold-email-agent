import pytest
from sqlalchemy import select

from cold_email.database import OUTREACH_SENDING, OUTREACH_SENT, DeadLetter
from cold_email.workers.logistics.logistics import logistics_task

OUTREACH_ID = "00000000-0000-0000-0000-000000000000"


@pytest.mark.asyncio
async def test_logistics_skips_when_outreach_not_found(async_session, sync_session_for):
    """Nothing to send, and nothing to fail -- the row simply doesn't exist."""
    result = logistics_task(OUTREACH_ID)
    assert result == {"status": "skipped", "reason": "not_found"}


@pytest.mark.asyncio
async def test_logistics_skips_when_not_claimed(
    async_session, admin_user_id, sync_session_for, pending_views
):
    """A row still 'approved' (not yet claimed 'sending' by send_due_task) is a
    no-op -- the second guard against a duplicate Celery delivery."""
    from cold_email.database import OUTREACH_APPROVED, Company, Outreach

    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()
    outreach = Outreach(user_id=admin_user_id, company_id=company.id, status=OUTREACH_APPROVED)
    async_session.add(outreach)
    await async_session.commit()

    result = logistics_task(str(outreach.id))
    assert result == {"status": "skipped", "reason": "not_claimed"}


@pytest.mark.asyncio
async def test_no_draft_to_send_fails_the_outreach_row(
    async_session, admin_user_id, sync_session_for, pending_views
):
    """Approved and claimed but no draft to send -- an anomaly, not a no-op."""
    from sqlalchemy import select

    from cold_email.database import OUTREACH_FAILED, Company, DeadLetter, Outreach

    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()
    outreach = Outreach(user_id=admin_user_id, company_id=company.id, status=OUTREACH_SENDING)
    async_session.add(outreach)
    await async_session.commit()

    logistics_task(str(outreach.id))

    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_FAILED

    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert dl.outreach_id == outreach.id
    assert dl.company_id is None
    assert dl.stage == "logistics"


@pytest.mark.asyncio
async def test_logistics_fails_when_owning_user_has_no_gmail_connected(
    async_session, admin_user_id, sync_session_for, pending_views, monkeypatch
):
    """The owning user's credentials are absent (never connected, or revoked)
    -> terminal failure, dead-lettered, no send attempted. Never falls back to
    a global mailbox or the caller's own credentials."""
    from sqlalchemy import select

    from cold_email.database import Company, CompanyContact, DeadLetter, Draft, Outreach
    from cold_email.workers.logistics.constants import ERR_GMAIL_DISCONNECTED

    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()
    contact = CompanyContact(company_id=company.id, email="c@acme.com", eligible=True)
    async_session.add(contact)
    await async_session.commit()
    outreach = Outreach(
        user_id=admin_user_id,
        company_id=company.id,
        contact_id=contact.id,
        status=OUTREACH_SENDING,
    )
    async_session.add(outreach)
    await async_session.commit()
    async_session.add(
        Draft(outreach_id=outreach.id, subject_line="Hi", body="Body", gmail_draft_id="gd-1")
    )
    await async_session.commit()

    def must_not_send(creds, draft_id):
        raise AssertionError("send_draft must not be called without credentials")

    monkeypatch.setattr("cold_email.workers.logistics.logistics.send_draft", must_not_send)

    result = logistics_task(str(outreach.id))

    assert result == {"status": "failed", "error": ERR_GMAIL_DISCONNECTED}
    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert dl.error_msg == ERR_GMAIL_DISCONNECTED
    assert dl.stage == "logistics"


@pytest.mark.asyncio
async def test_logistics_sends_existing_draft_and_advances_to_sent(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    """The happy path: a claimed row with a draft and owner credentials gets
    sent from the OWNING user's mailbox and advances to 'sent'."""
    outreach = await approved_outreach_factory(scheduled_send_at=None)
    outreach.status = OUTREACH_SENDING
    await async_session.commit()

    sent = []
    monkeypatch.setattr(
        "cold_email.workers.logistics.logistics.send_draft",
        lambda creds, draft_id: sent.append((creds.sender_email, draft_id)) or "msg-1",
    )

    result = logistics_task(str(outreach.id))

    assert result == {"status": "success", "message_id": "msg-1"}
    assert len(sent) == 1
    assert sent[0][0] == "admin@example.com"

    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_SENT


@pytest.mark.asyncio
async def test_post_send_status_update_failure_does_not_resend_or_fail(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    """Gmail accepts the message, then update_outreach_status(SENT) raises
    (a Cloud SQL blip, a SIGTERM between the two calls). The email already
    went out -- this must not be reported as 'failed' (that would falsely
    claim it was never sent), and a redelivery of the same task must not
    call send_draft a second time."""
    outreach = await approved_outreach_factory(scheduled_send_at=None)
    outreach.status = OUTREACH_SENDING
    await async_session.commit()

    sent = []
    monkeypatch.setattr(
        "cold_email.workers.logistics.logistics.send_draft",
        lambda creds, draft_id: sent.append(draft_id) or "msg-1",
    )

    def boom(outreach_id, status, error_msg=None):
        raise RuntimeError("db blip")

    monkeypatch.setattr("cold_email.workers.logistics.logistics.update_outreach_status", boom)

    result = logistics_task(str(outreach.id))
    assert result["status"] == "unknown"
    assert result["message_id"] == "msg-1"
    assert len(sent) == 1

    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_SENDING  # not 'sent', not 'failed'

    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert dl.stage == "logistics"
    assert "unknown" in dl.error_msg.lower()

    # A redelivery of the SAME task (Celery at-least-once, or the row simply
    # being reprocessed) must not send again: the ticket is already spent.
    result2 = logistics_task(str(outreach.id))
    assert result2 == {"status": "skipped", "reason": "already_claimed"}
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_send_draft_network_error_is_dead_lettered_not_resent(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    """A non-HttpError exception from send_draft (timeout, connection reset)
    means we cannot tell whether Gmail received the request. Never resend."""
    outreach = await approved_outreach_factory(scheduled_send_at=None)
    outreach.status = OUTREACH_SENDING
    await async_session.commit()

    calls = []

    def boom(creds, draft_id):
        calls.append(draft_id)
        raise TimeoutError("connection reset")

    monkeypatch.setattr("cold_email.workers.logistics.logistics.send_draft", boom)

    result = logistics_task(str(outreach.id))
    assert result["status"] == "unknown"
    assert len(calls) == 1

    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_SENDING  # not 'failed'

    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert "unknown" in dl.error_msg.lower()

    # Retrying must not call send_draft again -- the ticket is spent.
    result2 = logistics_task(str(outreach.id))
    assert result2 == {"status": "skipped", "reason": "already_claimed"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concurrent_executions_of_the_same_row_send_exactly_once(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    """Two concurrent deliveries of the SAME already-'sending' row (Celery's
    at-least-once delivery can redeliver one message to two workers) must not
    both reach send_draft. The status re-check alone cannot catch this --
    both would read 'sending' before either advances it -- so it is the claim
    on the draft's gmail_draft_id that serializes them."""
    from cold_email.workers.logistics import logistics as logistics_module

    outreach = await approved_outreach_factory(scheduled_send_at=None)
    outreach.status = OUTREACH_SENDING
    await async_session.commit()

    # Freeze what BOTH "concurrent" executions see when they read the send
    # row -- i.e. as if both read it before either had claimed anything.
    frozen_row = logistics_module.fetch_send_row(str(outreach.id))
    monkeypatch.setattr(logistics_module, "fetch_send_row", lambda oid: frozen_row)
    # Keep status at 'sending' across both calls so the SECOND execution
    # still passes the first guard too, same as it would mid-race in
    # production before either has written 'sent'.
    monkeypatch.setattr(logistics_module, "update_outreach_status", lambda *a, **k: None)

    sent = []
    monkeypatch.setattr(
        logistics_module,
        "send_draft",
        lambda creds, draft_id: sent.append(draft_id) or "msg-1",
    )

    result_a = logistics_module.logistics_task(str(outreach.id))
    result_b = logistics_module.logistics_task(str(outreach.id))

    assert len(sent) == 1
    assert {result_a["status"], result_b["status"]} == {"success", "skipped"}


@pytest.mark.asyncio
async def test_pre_send_db_failure_propagates_for_autoretry(
    async_session, approved_outreach_factory, sync_session_for, monkeypatch
):
    """A failure BEFORE the send ticket is claimed (looking up the owning
    user's credentials, here) is safe to retry -- nothing has been claimed or
    sent yet -- so it must propagate for Celery's autoretry_for to catch,
    not be swallowed the way a post-send failure is."""
    outreach = await approved_outreach_factory(scheduled_send_at=None)
    outreach.status = OUTREACH_SENDING
    await async_session.commit()

    def boom(user_id):
        raise RuntimeError("db connectivity blip")

    monkeypatch.setattr("cold_email.workers.logistics.logistics.fetch_owning_user", boom)

    with pytest.raises(RuntimeError, match="db connectivity blip"):
        logistics_task(str(outreach.id))

    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_SENDING  # untouched -- safe to retry


@pytest.mark.asyncio
async def test_claim_send_ticket_is_a_compare_and_swap(
    async_session, approved_outreach_factory, sync_session_for
):
    """The primitive Fix 1+2 relies on: the first caller to present the
    current gmail_draft_id wins and nulls it; a second caller presenting the
    SAME (now stale) value gets nothing."""
    from cold_email.workers.logistics.helpers.db_helpers import claim_send_ticket, fetch_send_row

    outreach = await approved_outreach_factory(scheduled_send_at=None)
    row = fetch_send_row(str(outreach.id))

    assert claim_send_ticket(row.draft_id, row.gmail_draft_id) is True
    assert claim_send_ticket(row.draft_id, row.gmail_draft_id) is False
