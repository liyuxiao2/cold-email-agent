"""Hunter.io Email Finder — resolve a founder's work email from name + domain.

The research stage has the founder's name and the company URL but no email; the
directory sources don't publish one. Hunter's Email Finder takes a domain + full
name and returns the most likely work email plus a confidence score. We call it
after LLM extraction and persist the result, so drafting has a real address.
"""

import logging
from urllib.parse import urlparse

import requests

from cold_email.config import settings
from cold_email.workers.research.constants import (
    HUNTER_EMAIL_FINDER_URL,
    HUNTER_TIMEOUT_SECONDS,
    MIN_EMAIL_SCORE,
)

logger = logging.getLogger(__name__)



def domain_from_url(url: str | None) -> str | None:
    """Extract a bare domain (no scheme, no www, no path) from a company URL."""
    if not url:
        return None
    parsed = urlparse(url if "//" in url else f"//{url}")
    host = (parsed.netloc or parsed.path).strip().lower()
    host = host.removeprefix("www.")
    return host.split("/")[0] or None


def find_email(full_name: str | None, domain: str | None) -> dict | None:
    """Look up a work email via Hunter Email Finder.

    Returns {"email": str, "score": int} when Hunter returns an address, else
    None (missing inputs, no match, or API error — all non-fatal: the caller
    gates on the result). `score` is Hunter's 0-100 deliverability confidence.
    """
    if not full_name or not domain or not settings.hunter_api_key:
        return None

    try:
        resp = requests.get(
            HUNTER_EMAIL_FINDER_URL,
            params={
                "domain": domain,
                "full_name": full_name,
                "api_key": settings.hunter_api_key,
            },
            timeout=HUNTER_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
    except (requests.RequestException, ValueError) as e:
        logger.error(f"Hunter lookup failed for {full_name} @ {domain}: {e}")
        return None

    email = data.get("email")
    if not email:
        logger.info(f"Hunter found no email for {full_name} @ {domain}")
        return None

    return {"email": email, "score": data.get("score") or 0}


def should_accept_email(result: dict | None) -> bool:
    """Decide whether a Hunter result is a usable address, or the lead should
    fail fast into the DLQ.

    `result` is find_email's return: {"email": str, "score": int} or None.
    Accept only a real address whose Hunter confidence clears MIN_EMAIL_SCORE;
    a bounced cold email hurts sender reputation, so low-confidence guesses are
    dead-lettered (retryable) rather than sent.
    """
    if not result or not result.get("email"):
        return False
    return result["score"] >= MIN_EMAIL_SCORE
