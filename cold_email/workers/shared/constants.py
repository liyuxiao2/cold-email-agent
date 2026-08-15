"""Shared constants for Celery workers."""

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 60  # seconds

# How long generate_json waits for a token from the shared Redis bucket before
# treating that model exactly like a 429 and skipping to the next one in the
# fallback chain. Replaces LLM_RATE_LIMIT / LLM_MIN_INTERVAL_SECONDS, which
# each paced only ONE worker process with time.sleep / Celery's per-worker
# rate_limit=; the real constraint is a provider quota shared by every worker,
# every user, and every task type.
BUCKET_WAIT_SECONDS = 30.0
