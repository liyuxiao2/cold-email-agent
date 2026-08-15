"""Hunter.io Domain Search — build the emailable contact pool for a company.

Replaces the old Email Finder call. /v2/email-finder takes name + domain and
returns exactly ONE address, so it structurally cannot produce a pool — and a
shared company pool with one address per company means every user emails the
same founder. /v2/domain-search takes a domain and returns many contacts with
positions, seniority, and confidence.

Contacts are classified but ALL of them are stored by the caller. Loosening
DECISION_MAKER_PATTERNS later can then re-classify stored rows instead of
re-spending Hunter credits.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from cold_email.config import settings
from cold_email.workers.research.constants import (
    DECISION_MAKER_PATTERNS,
    HUNTER_DOMAIN_SEARCH_LIMIT,
    HUNTER_DOMAIN_SEARCH_URL,
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


@dataclass(frozen=True)
class HunterContact:
    """One raw contact as Hunter returned it."""

    email: str
    first_name: str | None
    last_name: str | None
    position: str | None
    seniority: str | None
    department: str | None
    confidence: int
    is_generic: bool


@dataclass(frozen=True)
class ClassifiedContact:
    """A HunterContact plus our two derived flags."""

    contact: HunterContact
    is_founder: bool
    eligible: bool

    # Convenience passthroughs so callers can treat this as one object.
    @property
    def email(self) -> str:
        return self.contact.email


def looks_like_person_name(name: str | None) -> bool:
    """True if `name` is a plausible single 'First Last'.

    Kept from the Email Finder era, but its job changed: it no longer gates an
    API call, it decides whether the LLM-extracted founder_name is trustworthy
    enough to match against Hunter's results. Matching "the founders" against a
    contact would flag an arbitrary person as the founder.
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


def find_contacts(domain: str | None) -> list[HunterContact]:
    """Fetch every contact Hunter knows for a domain.

    Returns [] on missing inputs or any API error — non-fatal, matching the old
    find_email contract. The caller gates on `has_eligible_contact`.
    """
    if not domain or not settings.hunter_api_key:
        return []

    try:
        response = requests.get(
            HUNTER_DOMAIN_SEARCH_URL,
            params={
                "domain": domain,
                "limit": HUNTER_DOMAIN_SEARCH_LIMIT,
                "api_key": settings.hunter_api_key,
            },
            timeout=HUNTER_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.error(f"Hunter domain-search returned {response.status_code} for {domain}")
            return []
        emails = response.json().get("data", {}).get("emails", [])
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Hunter domain-search failed for {domain}: {exc}")
        return []

    contacts = [
        HunterContact(
            email=entry["value"],
            first_name=entry.get("first_name"),
            last_name=entry.get("last_name"),
            position=entry.get("position"),
            seniority=entry.get("seniority"),
            department=entry.get("department"),
            confidence=entry.get("confidence") or 0,
            is_generic=entry.get("type") == "generic",
        )
        for entry in emails
        if entry.get("value")
    ]
    logger.info(f"Hunter returned {len(contacts)} contacts for {domain}")
    return contacts


# The three-letter C-suite acronyms are also substrings of common non-decision
# titles — "cto" sits inside "dire-cto-r", "coo" starts "coo-rdinator" — so
# plain substring matching against DECISION_MAKER_PATTERNS would wrongly
# qualify a Creative/Art/Finance Director or an Office/Sales/Warehouse
# Coordinator. Multi-word patterns like "head of engineering" have no such
# collision risk, so they keep the simple (and desirable) substring match.
# Acronyms alone get a word-boundary regex instead.
_ACRONYM_PATTERNS = frozenset({"ceo", "cto", "coo"})
_SUBSTRING_PATTERNS = tuple(p for p in DECISION_MAKER_PATTERNS if p not in _ACRONYM_PATTERNS)
_ACRONYM_RE = re.compile(r"\b(?:" + "|".join(sorted(_ACRONYM_PATTERNS)) + r")\b", re.IGNORECASE)


def _is_decision_maker(position: str | None) -> bool:
    if not position:
        return False
    lowered = position.lower()
    if _ACRONYM_RE.search(lowered):
        return True
    return any(pattern in lowered for pattern in _SUBSTRING_PATTERNS)


def _is_founder_position(position: str | None) -> bool:
    return bool(position) and "founder" in position.lower()


def _matches_founder(contact: HunterContact, founder_name: str | None) -> bool:
    if not looks_like_person_name(founder_name):
        return False
    full = f"{contact.first_name or ''} {contact.last_name or ''}".strip().lower()
    return bool(full) and full == founder_name.strip().lower()


def classify_contacts(
    contacts: list[HunterContact], founder_name: str | None
) -> list[ClassifiedContact]:
    """Derive is_founder and eligible for every contact.

    Eligible requires ALL of:
      1. not a generic catch-all (info@, support@) — those reply poorly and land
         in a shared queue
      2. confidence >= MIN_EMAIL_SCORE — a bounce hurts sender reputation
      3. a decision-maker/hiring position, OR (no position on file AND is_founder)

    The founder bypass only fires when Hunter didn't return a title at all: a
    name-match against the LLM's founder_name is a weaker signal than a title
    Hunter actually observed, so a known non-decision-maker title (e.g. "Staff
    Accountant") is not overridden just because the name happens to match.
    """
    classified = []
    for contact in contacts:
        is_founder = _matches_founder(contact, founder_name) or _is_founder_position(
            contact.position
        )
        eligible = (
            not contact.is_generic
            and contact.confidence >= MIN_EMAIL_SCORE
            and (_is_decision_maker(contact.position) or (not contact.position and is_founder))
        )
        classified.append(
            ClassifiedContact(contact=contact, is_founder=is_founder, eligible=eligible)
        )
    return classified


def has_eligible_contact(contacts: list[ClassifiedContact]) -> bool:
    """True if at least one contact is worth emailing.

    Replaces should_accept_email as research's fail-fast gate: no eligible
    contact means nobody can email this company, so it is dead-lettered at
    research rather than wasting the drafting stage.
    """
    return any(c.eligible for c in contacts)
