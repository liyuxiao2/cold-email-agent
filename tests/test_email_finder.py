from unittest.mock import MagicMock, patch

from cold_email.workers.research.helpers.email_finder import (
    domain_from_url,
    find_email,
    should_accept_email,
)


def test_domain_from_url_strips_scheme_www_and_path():
    assert domain_from_url("https://www.acme.io/about") == "acme.io"
    assert domain_from_url("http://acme.io") == "acme.io"
    assert domain_from_url("acme.io/team") == "acme.io"
    assert domain_from_url(None) is None
    assert domain_from_url("") is None


def test_find_email_returns_none_without_inputs():
    # No HTTP call should happen when name/domain are missing.
    with patch("cold_email.workers.research.helpers.email_finder.requests.get") as get:
        assert find_email(None, "acme.io") is None
        assert find_email("Ada Lovelace", None) is None
        get.assert_not_called()


def test_find_email_parses_hunter_response():
    resp = MagicMock()
    resp.json.return_value = {"data": {"email": "ada@acme.io", "score": 92}}
    with (
        patch("cold_email.workers.research.helpers.email_finder.settings.hunter_api_key", "k"),
        patch("cold_email.workers.research.helpers.email_finder.requests.get", return_value=resp),
    ):
        assert find_email("Ada Lovelace", "acme.io") == {"email": "ada@acme.io", "score": 92}


def test_find_email_none_when_hunter_finds_nothing():
    resp = MagicMock()
    resp.json.return_value = {"data": {"email": None}}
    with (
        patch("cold_email.workers.research.helpers.email_finder.settings.hunter_api_key", "k"),
        patch("cold_email.workers.research.helpers.email_finder.requests.get", return_value=resp),
    ):
        assert find_email("Ada Lovelace", "acme.io") is None


def test_should_accept_email_gate():
    # None (no match / API error) and missing email are rejected.
    assert should_accept_email(None) is False
    assert should_accept_email({"score": 99}) is False
    # Score gates on MIN_EMAIL_SCORE (50).
    assert should_accept_email({"email": "a@acme.io", "score": 80}) is True
    assert should_accept_email({"email": "a@acme.io", "score": 50}) is True
    assert should_accept_email({"email": "a@acme.io", "score": 20}) is False
