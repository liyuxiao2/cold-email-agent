import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from cold_email.auth.session import (
    SESSION_COOKIE,
    SessionSecretMissing,
    mint_session,
    mint_state,
    verify_session,
    verify_state,
)
from cold_email.config import settings


def test_session_round_trip():
    user_id = uuid.uuid4()
    assert verify_session(mint_session(user_id)) == user_id


def test_cookie_name():
    assert SESSION_COOKIE == "ce_session"


def test_expired_session_rejected():
    payload = {
        "sub": str(uuid.uuid4()),
        "exp": datetime.now(UTC) - timedelta(seconds=1),
        "typ": "session",
    }
    token = jwt.encode(payload, settings.session_secret, algorithm="HS256")
    assert verify_session(token) is None


def test_session_signed_with_other_secret_rejected():
    payload = {
        "sub": str(uuid.uuid4()),
        "exp": datetime.now(UTC) + timedelta(days=1),
        "typ": "session",
    }
    token = jwt.encode(payload, "an-attackers-secret", algorithm="HS256")
    assert verify_session(token) is None


def test_malformed_session_rejected():
    assert verify_session("not-a-jwt") is None


def test_state_nonce_round_trip():
    assert verify_state(mint_state()) is True


def test_tampered_state_rejected():
    assert verify_state(mint_state() + "x") is False


def test_state_is_not_accepted_as_a_session():
    """A state nonce must not be usable as a session, or anyone who can start
    a login could mint themselves a session for an arbitrary user id."""
    assert verify_session(mint_state()) is None


@pytest.mark.parametrize("bad", ["", "  ", None])
def test_empty_state_rejected(bad):
    assert verify_state(bad) is False


def test_missing_session_secret_raises_a_clear_error(monkeypatch):
    """An empty SESSION_SECRET must fail loudly and identifiably, not as a
    generic 401/500 indistinguishable from a broken frontend."""
    monkeypatch.setattr(settings, "session_secret", "")
    with pytest.raises(SessionSecretMissing):
        mint_session(uuid.uuid4())
    with pytest.raises(SessionSecretMissing):
        verify_session("some-token")
    with pytest.raises(SessionSecretMissing):
        mint_state()
    with pytest.raises(SessionSecretMissing):
        verify_state("some-state")
