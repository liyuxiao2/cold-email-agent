"""Constants for the drafting worker (magic values only, no logic)."""

# Model used for email generation. Kept local to the worker so drafting and
# research can diverge on model choice without coupling their constants.
MODEL_NAME = "gemini-2.5-flash"

# Gemini returns the tool payload as a JSON string, sometimes wrapped in a
# markdown code fence. These markers let us strip the fence before json.loads.
JSON_BLOCK_START_MARKER = "```json"
JSON_BLOCK_END_MARKER = "```"

# The Lead table has no founder title; startups.gallery leads are founders, so
# this is the default recipient title injected into the draft prompt.
DEFAULT_FOUNDER_TITLE = "Founder"

# Terminal per-lead failure reasons — written to leads.error_msg so a bad lead
# is excluded from future sweeps instead of retried forever.
ERR_NO_FOUNDER_EMAIL = "No founder email to draft to"
ERR_EMPTY_DRAFT = "Model returned an empty draft"
