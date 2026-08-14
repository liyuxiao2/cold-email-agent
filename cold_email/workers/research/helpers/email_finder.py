"""Hunter.io Email Finder — resolve a founder's work email from name + domain.

The research stage has the founder's name and the company URL but no email; the
directory sources don't publish one. Hunter's Email Finder takes a domain + full
name and returns the most likely work email plus a confidence score. We call it
after LLM extraction and persist the result, so drafting has a real address.
"""

import logging
import re
from urllib.parse import urlparse

import requests

from cold_email.config import settings
from cold_email.workers.research.constants import (
    HUNTER_EMAIL_FINDER_URL,
    HUNTER_TIMEOUT_SECONDS,
    MIN_EMAIL_SCORE,
)

logger = logging.getLogger(__name__)

# Tokens that signal the LLM returned a non-name (title, hedge, or placeholder)
# rather than a person. Matched case-insensitively against whole words.
_NON_NAME_TOKENS = {
    "not",
    "founder",
    "founders",
    "ceo",
    "cto",
    "coo",
    "cofounder",
    "co-founder",
    "the",
    "team",
    "board",
    "director",
    "directors",
    "unknown",
    "none",
    "na",
    "n/a",
    "unclear",
    "unnamed",
    "and",
}
_NAME_WORD = re.compile(r"^[A-Za-z][A-Za-z.'-]*$")


def looks_like_person_name(name: str | None) -> bool:
    """True if `name` is a plausible single 'First Last' to hand Hunter.

    Hunter's Email Finder needs one clean personal name. The LLM sometimes emits
    a title, a hedge sentence, or a comma-list of founders; feeding those returns
    nothing and wastes a call, so we reject anything that isn't 2-4 name-like
    words free of non-name tokens.
    """
    if not name:
        return False
    name = name.strip()
    if "," in name or len(name) > 40:
        return False
    words = name.split()
    if not (2 <= len(words) <= 4):
        return False
    if not all(_NAME_WORD.match(w) for w in words):
        return False
    return not ({w.lower().strip(".") for w in words} & _NON_NAME_TOKENS)


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
    if not domain or not settings.hunter_api_key:
        return None
    if not looks_like_person_name(full_name):
        logger.info(f"Skipping Hunter — {full_name!r} is not a usable person name")
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
