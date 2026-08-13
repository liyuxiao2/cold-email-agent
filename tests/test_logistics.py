from unittest.mock import patch

from cold_email.workers.logistics.logistics import logistics_task
from cold_email.workers.shared.views import PendingSend

LEAD_ID = "00000000-0000-0000-0000-000000000000"


def test_logistics_skips_when_not_pending():
    """A lead absent from pending_sends (not approved / already sent) is a no-op."""
    with (
        patch(
            "cold_email.workers.logistics.logistics.fetch_send_inputs", return_value=None
        ),
        patch("cold_email.workers.logistics.logistics.send_draft") as mock_send,
        patch("cold_email.workers.logistics.logistics.update_lead_status") as mock_status,
    ):
        result = logistics_task.apply(args=[LEAD_ID]).get(propagate=True)

    assert result == {"status": "skipped", "reason": "lead not pending send"}
    mock_send.assert_not_called()
    mock_status.assert_not_called()


def test_logistics_sends_existing_draft():
    """An approved lead with a gmail_draft_id gets sent and advanced to 'sent'."""
    with (
        patch(
            "cold_email.workers.logistics.logistics.fetch_send_inputs",
            return_value=PendingSend(
                lead_id=LEAD_ID,
                founder_email="founder@acme.com",
                gmail_draft_id="gmail-123",
                subject_line="Hi",
                body="Body",
            ),
        ),
        patch(
            "cold_email.workers.logistics.logistics.send_draft", return_value="msg-1"
        ) as mock_send,
        patch("cold_email.workers.logistics.logistics.update_lead_status") as mock_status,
    ):
        result = logistics_task.apply(args=[LEAD_ID]).get(propagate=True)

    assert result == {"status": "success"}
    mock_send.assert_called_once_with("gmail-123")
    mock_status.assert_called_once_with(LEAD_ID, "sent")


def test_logistics_fails_without_gmail_draft_id():
    """Approved but no gmail_draft_id → terminal failure, no send attempted."""
    with (
        patch(
            "cold_email.workers.logistics.logistics.fetch_send_inputs",
            return_value=PendingSend(
                lead_id=LEAD_ID,
                founder_email="founder@acme.com",
                gmail_draft_id=None,
                subject_line="Hi",
                body="Body",
            ),
        ),
        patch("cold_email.workers.logistics.logistics.send_draft") as mock_send,
        patch(
            "cold_email.workers.logistics.logistics.handle_terminal_failure"
        ) as mock_terminal,
    ):
        result = logistics_task.apply(args=[LEAD_ID]).get(propagate=True)

    assert result["status"] == "failed"
    mock_send.assert_not_called()
    # handle_terminal_failure requires keyword-only stage/task_name — assert they
    # are passed so the real (unmocked) call can't raise TypeError in production.
    assert mock_terminal.call_args.args[0] == LEAD_ID
    assert mock_terminal.call_args.kwargs["stage"] == "logistics"
    assert mock_terminal.call_args.kwargs["task_name"] == (
        "cold_email.workers.logistics.logistics_task"
    )
