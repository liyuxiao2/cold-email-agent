"""Shared constants for Celery workers."""

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 60  # seconds

# Gemini free tier is per-model-per-minute, and generate_with_fallback rotates
# across a chain of models (~43 RPM combined). These values pace each consumer
# near the most-generous single model's limit; the fallback absorbs bursts that
# briefly exceed one model, and Celery retry is the final backstop.
#   - research: per-lead Celery rate_limit
#   - drafting: sleep between calls in the batch sweep
GEMINI_RATE_LIMIT = "15/m"
GEMINI_MIN_INTERVAL_SECONDS = 4
