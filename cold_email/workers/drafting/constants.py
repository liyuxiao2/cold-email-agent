"""Constants for the drafting worker (magic values only, no logic)."""

# Gemini returns the tool payload as a JSON string, sometimes wrapped in a
# markdown code fence. These markers let us strip the fence before json.loads.
JSON_BLOCK_START_MARKER = "```json"
JSON_BLOCK_END_MARKER = "```"

# Terminal per-lead failure reasons — written to leads.error_msg so a bad lead
# is excluded from future sweeps instead of retried forever.
ERR_NO_FOUNDER_EMAIL = "No founder email to draft to"
ERR_EMPTY_DRAFT = "Model returned an empty draft"
