import httpx
import pytest

from cold_email.workers.research.constants import DECISION_MAKER_PATTERNS
from cold_email.workers.research.helpers.contact_finder import (
    HunterContact,
    classify_contacts,
    domain_from_url,
    find_contacts,
    has_eligible_contact,
    looks_like_person_name,
)


def _contact(**overrides) -> HunterContact:
    base = {
        "email": "person@acme.com",
        "first_name": "Ann",
        "last_name": "Reed",
        "position": "CTO",
        "seniority": "executive",
        "department": "it",
        "confidence": 90,
        "is_generic": False,
    }
    return HunterContact(**{**base, **overrides})


# ---------------------------------------------------------------- eligibility


def test_generic_addresses_are_ineligible():
    """info@ and support@ land in a shared queue and reply poorly."""
    [c] = classify_contacts([_contact(email="info@acme.com", is_generic=True)], "Ann Reed")
    assert c.eligible is False


def test_sub_threshold_confidence_is_ineligible():
    """MIN_EMAIL_SCORE is unchanged at 25; it is now a per-contact filter."""
    [c] = classify_contacts([_contact(confidence=10)], "Ann Reed")
    assert c.eligible is False


def test_non_decision_maker_position_is_ineligible():
    [c] = classify_contacts([_contact(position="Staff Accountant")], "Ann Reed")
    assert c.eligible is False


@pytest.mark.parametrize("pattern", DECISION_MAKER_PATTERNS)
def test_every_decision_maker_pattern_is_eligible(pattern):
    [c] = classify_contacts([_contact(position=pattern.title())], "Ann Reed")
    assert c.eligible is True, f"pattern not matched: {pattern}"


def test_missing_position_is_ineligible_unless_founder():
    [c] = classify_contacts([_contact(position=None)], "Zed Other")
    assert c.eligible is False

    [f] = classify_contacts([_contact(position=None)], "Ann Reed")
    assert f.is_founder is True
    assert f.eligible is True


# ------------------------------------------------------------------ is_founder


def test_is_founder_by_name_match():
    [c] = classify_contacts([_contact(position="Engineer")], "Ann Reed")
    assert c.is_founder is True


def test_name_match_is_case_insensitive():
    [c] = classify_contacts([_contact(first_name="ANN", last_name="reed")], "Ann Reed")
    assert c.is_founder is True


def test_is_founder_by_position():
    [c] = classify_contacts([_contact(position="Co-Founder")], "Someone Else")
    assert c.is_founder is True


def test_unusable_founder_name_does_not_match_anyone():
    """looks_like_person_name survives the Hunter switch precisely for this:
    'the founders' must not be matched against a contact."""
    [c] = classify_contacts([_contact(position="Engineer")], "the founders")
    assert c.is_founder is False


# ------------------------------------------------------------------ fail-fast


def test_has_eligible_contact_false_for_all_generic():
    contacts = classify_contacts(
        [
            _contact(email="info@acme.com", is_generic=True, position=None),
            _contact(email="support@acme.com", is_generic=True, position=None),
        ],
        "Zed Other",
    )
    assert has_eligible_contact(contacts) is False


def test_has_eligible_contact_true_when_one_qualifies():
    contacts = classify_contacts(
        [_contact(email="info@acme.com", is_generic=True, position=None), _contact()],
        "Zed Other",
    )
    assert has_eligible_contact(contacts) is True


# ------------------------------------------------------------- Hunter mapping


def test_find_contacts_maps_the_hunter_payload(monkeypatch):
    payload = {
        "data": {
            "domain": "acme.com",
            "emails": [
                {
                    "value": "ann@acme.com",
                    "first_name": "Ann",
                    "last_name": "Reed",
                    "position": "CTO",
                    "seniority": "executive",
                    "department": "it",
                    "confidence": 92,
                    "type": "personal",
                },
                {
                    "value": "info@acme.com",
                    "first_name": None,
                    "last_name": None,
                    "position": None,
                    "seniority": None,
                    "department": None,
                    "confidence": 70,
                    "type": "generic",
                },
            ],
        }
    }
    monkeypatch.setattr(
        "cold_email.workers.research.helpers.contact_finder.requests.get",
        lambda *a, **k: httpx.Response(200, json=payload),
    )
    contacts = find_contacts("acme.com")
    assert len(contacts) == 2
    assert contacts[0].email == "ann@acme.com"
    assert contacts[0].confidence == 92
    assert contacts[0].is_generic is False
    assert contacts[1].is_generic is True


def test_find_contacts_returns_empty_on_network_error(monkeypatch):
    """Non-fatal, matching the old find_email contract: the caller gates on the
    result rather than the call raising."""
    import requests

    def boom(*a, **k):
        raise requests.RequestException("timeout")

    monkeypatch.setattr("cold_email.workers.research.helpers.contact_finder.requests.get", boom)
    assert find_contacts("acme.com") == []


def test_find_contacts_without_a_domain_makes_no_call():
    assert find_contacts(None) == []


# ------------------------------------- carried over from test_email_finder.py


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.acme.com/about", "acme.com"),
        ("http://acme.com", "acme.com"),
        ("acme.com/team", "acme.com"),
        ("https://sub.acme.co.uk/", "sub.acme.co.uk"),
        (None, None),
        ("", None),
    ],
)
def test_domain_from_url(url, expected):
    assert domain_from_url(url) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Ann Reed", True),
        ("Ann", False),
        ("Ann Reed, Bo Lin", False),
        ("the founders", False),
        ("CEO", False),
        (None, False),
        ("", False),
    ],
)
def test_looks_like_person_name(name, expected):
    assert looks_like_person_name(name) is expected
