"""Shared constants for Celery workers."""

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 60  # seconds

# The Gemini free tier allows 5 generate_content requests/min per model per
# project, shared across research + drafting. Stay under it: research paces
# per-lead task execution via Celery's rate_limit; the drafting batch sweep
# sleeps between calls. ~4/min leaves headroom for occasional overlap.
GEMINI_RATE_LIMIT = "4/m"
GEMINI_MIN_INTERVAL_SECONDS = 15
