"""Constants for the logistics worker (magic values only, no logic)."""

# Terminal per-lead failure reason — written to leads.error_msg when a lead was
# approved but has no Gmail draft to send (should not happen in a healthy flow).
ERR_NO_GMAIL_DRAFT = "Approved lead has no Gmail draft to send"

LOGISTICS = "logistics"
