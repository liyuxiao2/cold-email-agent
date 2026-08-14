"""Constants for the drafting worker (magic values only, no logic)."""

# Terminal per-lead failure reasons — written to leads.error_msg so a bad lead
# is excluded from future sweeps instead of retried forever.
ERR_NO_FOUNDER_EMAIL = "No founder email to draft to"
ERR_EMPTY_DRAFT = "Model returned an empty draft"
DRAFTING = "drafting"
