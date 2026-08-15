"""Constants for the drafting worker (magic values only, no logic)."""

# Terminal per-outreach failure reason — written to outreach.error_msg so a bad
# row is excluded from future sweeps instead of retried forever.
ERR_EMPTY_DRAFT = "Model returned an empty draft"
DRAFTING = "drafting"
