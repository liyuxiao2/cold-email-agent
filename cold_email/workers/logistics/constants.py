"""Constants for the logistics worker (magic values only, no logic)."""

# Terminal per-outreach failure reason — written to outreach.error_msg when an
# outreach row was approved but has no Gmail draft to send (should not happen
# in a healthy flow — either drafting never ran, or the draft row is missing).
ERR_NO_GMAIL_DRAFT = "Approved outreach has no Gmail draft to send"

# Terminal per-outreach failure reason — the owning user has no usable Gmail
# refresh token (never connected, or revoked access since).
ERR_GMAIL_DISCONNECTED = "Gmail not connected for this user"

LOGISTICS = "logistics"
