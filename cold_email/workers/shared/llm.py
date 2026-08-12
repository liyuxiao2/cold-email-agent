"""Model fallback for Gemini generate_content calls.

Both research extraction and email drafting route their generate_content calls
through generate_with_fallback. It cycles the configured model chain and skips
past any model whose free-tier quota is exhausted (HTTP 429 /
RESOURCE_EXHAUSTED), so a single tapped-out model doesn't stall the pipeline.

The chain (settings.model_fallback_chain) is ordered most-generous-first, so we
spend the roomiest daily quota before falling back to tighter ones. When every
model in the chain is exhausted we re-raise the last 429 — the caller's Celery
task treats that as transient and retries on the next tick, by which point a
daily quota may have reset.
"""

import logging

from google import genai
from google.genai import errors

from cold_email.config import settings

logger = logging.getLogger(__name__)


def _should_fall_back(exc: Exception) -> bool:
    """True when `exc` means *this* model is unusable but another might work.

    Two cases advance to the next model in the chain:
      - 429 / RESOURCE_EXHAUSTED — this model's quota is tapped out.
      - 404 / NOT_FOUND — this model was retired or isn't available to this
        project. Model names drift over time (e.g. the 2.5-flash family 404s
        for new keys), so a dead model shouldn't abort the whole chain.

    Any other error (400 bad request, 401 auth, 5xx) won't be fixed by swapping
    models, so callers re-raise those immediately.
    """
    if isinstance(exc, errors.APIError) and getattr(exc, "code", None) in (404, 429):
        return True
    msg = str(exc)
    return "RESOURCE_EXHAUSTED" in msg or "NOT_FOUND" in msg


def generate_with_fallback(*, contents, config):
    """Run generate_content across the model fallback chain.

    Tries each model in order; when one is unusable (429 quota / 404 retired)
    logs and advances to the next. Re-raises the last such error if the whole
    chain is exhausted, and re-raises any other error immediately.
    """
    client = genai.Client(api_key=settings.gemini_api_key)
    chain = settings.model_fallback_chain or [settings.model_name]
    last_exc: Exception | None = None

    for model in chain:
        try:
            return client.models.generate_content(
                model=model, contents=contents, config=config
            )
        except Exception as exc:
            if not _should_fall_back(exc):
                raise
            last_exc = exc
            logger.warning(
                "Gemini model %s unavailable (quota/retired); falling back to next in chain",
                model,
            )

    logger.error("All %d models in fallback chain exhausted", len(chain))
    raise last_exc
