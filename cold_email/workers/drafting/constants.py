"""Constants for the drafting worker (magic values only, no logic)."""

# Terminal per-outreach failure reasons — written to outreach.error_msg so a bad
# row is excluded from future sweeps instead of retried forever.
ERR_NO_CONTACT_EMAIL = "No contact email to draft to"
ERR_EMPTY_DRAFT = "Model returned an empty draft"
DRAFTING = "drafting"
