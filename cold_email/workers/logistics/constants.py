"""Constants for the logistics worker (magic values only, no logic)."""

# Terminal per-outreach failure reason — written to outreach.error_msg when an
# outreach row was approved but has no Gmail draft to send (should not happen
# in a healthy flow — either drafting never ran, or the draft row is missing).
ERR_NO_GMAIL_DRAFT = "Approved outreach has no Gmail draft to send"

# Terminal per-outreach failure reason — the owning user has no usable Gmail
# refresh token (never connected, or revoked access since).
ERR_GMAIL_DISCONNECTED = "Gmail not connected for this user"

LOGISTICS = "logistics"

# The row was claimed 'sending' by send_due_task but a worker died before
# reaching 'sent' or 'failed' -- its outcome is unknown. Never auto-retried:
# retrying a send whose outcome is unknown is precisely how a double-send
# happens. A human must verify the mailbox first.
ERR_SEND_STATUS_UNKNOWN = (
    "Send status unknown — the worker was claimed but never completed. "
    "Verify the mailbox before retrying."
)

# Terminal per-outreach failure reason for a genuine Gmail API error while
# sending (e.g. the draft was deleted by hand between approving and the
# scheduled send).
ERR_SEND_FAILED = "Gmail send failed"
