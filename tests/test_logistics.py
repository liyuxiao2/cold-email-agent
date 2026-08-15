import pytest

from cold_email.database import OUTREACH_SENDING, OUTREACH_SENT
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
