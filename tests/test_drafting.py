from unittest.mock import patch

from cold_email.workers.drafting.drafting import drafting_task
from cold_email.workers.shared.views import PendingDraft

LEAD_A = "00000000-0000-0000-0000-00000000000a"
LEAD_B = "00000000-0000-0000-0000-00000000000b"


def _pending_row(lead_id, founder_email="founder@acme.com"):
    return PendingDraft(
        lead_id=lead_id,
        company_name="Acme",
        founder_name="Ada",
        founder_email=founder_email,
        company_url="https://acme.com",
        raw_content="Mock raw content",
        tech_stack="Python",
        recent_news="Raised a seed round",
        hook="Ledger scaling pain",
    )


def test_drafting_sweep_empty():
    """No pending leads → the sweep is a no-op returning drafted: 0."""
    with patch("cold_email.workers.drafting.drafting.fetch_pending_drafts", return_value=[]):
        result = drafting_task.apply(args=[]).get(propagate=True)
    assert result == {"status": "success", "drafted": 0}


def test_drafting_sweep_happy_path():
    """A draftable lead is generated, drafted in Gmail, persisted, and advanced."""
    with (
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(LEAD_A)],
        ),
        patch(
            "cold_email.workers.drafting.drafting.draft_email",
            return_value={
                "subject": "Hi",
                "body": "A specific, short note.",
                "body_html": "<p>A specific, short note.</p>",
            },
        ),
        patch(
            "cold_email.workers.drafting.drafting.create_draft", return_value="gmail-123"
        ) as mock_create,
        patch("cold_email.workers.drafting.drafting.commit_draft") as mock_commit,
        patch("cold_email.workers.drafting.drafting.update_lead_status") as mock_status,
        patch("cold_email.workers.drafting.drafting.time.sleep"),
    ):
        result = drafting_task.apply(args=[]).get(propagate=True)

    assert result == {"status": "success", "drafted": 1}
    _, kwargs = mock_create.call_args
    assert kwargs["to"] == "founder@acme.com"
    assert kwargs["subject"] == "Hi"
    assert kwargs["body"] == "A specific, short note."
    assert kwargs["html"] == "<p>A specific, short note.</p>"
    assert "attachment_path" in kwargs
    assert kwargs["attachment_path"].endswith("cold_email/resume.pdf")
    mock_commit.assert_called_once()
    mock_status.assert_called_once_with(LEAD_A, "drafted")


def test_drafting_skips_lead_without_email():
    """A lead with no founder_email is marked failed and never sent to the LLM."""
    with (
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(LEAD_A, founder_email=None)],
        ),
        patch("cold_email.workers.drafting.drafting.draft_email") as mock_draft,
        patch("cold_email.workers.drafting.drafting.handle_terminal_failure") as mock_terminal,
    ):
        result = drafting_task.apply(args=[]).get(propagate=True)

    assert result == {"status": "success", "drafted": 0}
    mock_draft.assert_not_called()
    mock_terminal.assert_called_once()
    assert mock_terminal.call_args.args[0] == LEAD_A


def test_drafting_marks_empty_draft_failed():
    """An empty/malformed model draft is terminal for that lead."""
    with (
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(LEAD_A)],
        ),
        patch("cold_email.workers.drafting.drafting.draft_email", return_value={}),
        patch("cold_email.workers.drafting.drafting.create_draft") as mock_create,
        patch("cold_email.workers.drafting.drafting.handle_terminal_failure") as mock_terminal,
        patch("cold_email.workers.drafting.drafting.time.sleep"),
    ):
        result = drafting_task.apply(args=[]).get(propagate=True)

    assert result == {"status": "success", "drafted": 0}
    mock_create.assert_not_called()
    assert mock_terminal.call_args.args[0] == LEAD_A


def test_drafting_one_bad_lead_does_not_abort_sweep():
    """A transient failure on one lead leaves it for the next sweep; others still draft."""
    with (
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(LEAD_A), _pending_row(LEAD_B)],
        ),
        patch(
            "cold_email.workers.drafting.drafting.draft_email",
            return_value={"subject": "Hi", "body": "Body"},
        ),
        # First create_draft raises (transient), second succeeds.
        patch(
            "cold_email.workers.drafting.drafting.create_draft",
            side_effect=[RuntimeError("gmail down"), "gmail-456"],
        ),
        patch("cold_email.workers.drafting.drafting.commit_draft"),
        patch("cold_email.workers.drafting.drafting.update_lead_status") as mock_status,
        patch("cold_email.workers.drafting.drafting.time.sleep"),
    ):
        result = drafting_task.apply(args=[]).get(propagate=True)

    # Only the second lead drafted; the first was left at 'researched' (no status write).
    assert result == {"status": "success", "drafted": 1}
    mock_status.assert_called_once_with(LEAD_B, "drafted")
