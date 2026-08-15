from unittest.mock import patch

import pytest

from cold_email.database import OUTREACH_SENT
from cold_email.workers.logistics.logistics import logistics_task
from cold_email.workers.shared.views import PendingSend

OUTREACH_ID = "00000000-0000-0000-0000-000000000000"


def test_logistics_skips_when_not_pending():
    """An outreach row absent from pending_sends because it isn't approved
    (queued/drafted/sent/rejected) is a no-op."""
    with (
        patch("cold_email.workers.logistics.logistics.fetch_send_inputs", return_value=None),
        patch(
            "cold_email.workers.logistics.logistics.fetch_outreach_status",
            return_value="drafted",
        ),
        patch("cold_email.workers.logistics.logistics.send_draft") as mock_send,
        patch("cold_email.workers.logistics.logistics.update_outreach_status") as mock_status,
    ):
        result = logistics_task(OUTREACH_ID)

    assert result == {"status": "skipped", "reason": "outreach not pending send"}
    mock_send.assert_not_called()
    mock_status.assert_not_called()


def test_logistics_sends_existing_draft():
    """An approved outreach row with a gmail_draft_id gets sent and advanced to 'sent'."""
    with (
        patch(
            "cold_email.workers.logistics.logistics.fetch_send_inputs",
            return_value=PendingSend(
                outreach_id=OUTREACH_ID,
                user_id="00000000-0000-0000-0000-000000000001",
                contact_email="contact@acme.com",
                gmail_draft_id="gmail-123",
                subject_line="Hi",
                body="Body",
            ),
        ),
        patch(
            "cold_email.workers.logistics.logistics.send_draft", return_value="msg-1"
        ) as mock_send,
        patch("cold_email.workers.logistics.logistics.update_outreach_status") as mock_status,
    ):
        result = logistics_task(OUTREACH_ID)

    assert result == {"status": "success"}
    mock_send.assert_called_once_with("gmail-123")
    mock_status.assert_called_once_with(OUTREACH_ID, OUTREACH_SENT)


def test_logistics_fails_without_gmail_draft_id():
    """Approved but no gmail_draft_id -> terminal failure, no send attempted."""
    with (
        patch(
            "cold_email.workers.logistics.logistics.fetch_send_inputs",
            return_value=PendingSend(
                outreach_id=OUTREACH_ID,
                user_id="00000000-0000-0000-0000-000000000001",
                contact_email="contact@acme.com",
                gmail_draft_id=None,
                subject_line="Hi",
                body="Body",
            ),
        ),
        patch("cold_email.workers.logistics.logistics.send_draft") as mock_send,
        patch("cold_email.workers.logistics.logistics.fail_outreach") as mock_fail,
    ):
        result = logistics_task(OUTREACH_ID)

    assert result["status"] == "failed"
    mock_send.assert_not_called()
    # fail_outreach requires keyword-only stage/task_name — assert they are
    # passed so the real (unmocked) call can't raise TypeError in production.
    assert mock_fail.call_args.args[0] == OUTREACH_ID
    assert mock_fail.call_args.kwargs["stage"] == "logistics"
    assert mock_fail.call_args.kwargs["task_name"] == (
        "cold_email.workers.logistics.logistics_task"
    )


@pytest.mark.asyncio
async def test_no_draft_to_send_fails_the_outreach_row(
    async_session, admin_user_id, sync_session_for, pending_views
):
    from cold_email.database import (
        OUTREACH_APPROVED,
        OUTREACH_FAILED,
        Company,
        DeadLetter,
        Outreach,
    )

    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()
    outreach = Outreach(user_id=admin_user_id, company_id=company.id, status=OUTREACH_APPROVED)
    async_session.add(outreach)
    await async_session.commit()

    logistics_task(str(outreach.id))

    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_FAILED

    from sqlalchemy import select

    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert dl.outreach_id == outreach.id
    assert dl.company_id is None
    assert dl.stage == "logistics"
