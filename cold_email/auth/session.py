"""Stateless session tokens and CSRF state nonces.

A session is an HS256 JWT in an httpOnly cookie: unreadable by JavaScript (so
an XSS cannot exfiltrate it) and verifiable with no database round-trip.

The tradeoff is that an individual token cannot be revoked before it expires.
Acceptable at a 7-day TTL with no billing or destructive admin actions. If
revocation becomes necessary, add a `session_version` integer to `users` and
embed it in the claim.

Both token kinds carry a `typ` claim and are verified against it, so a state
nonce can never be replayed as a session.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from cold_email.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "ce_session"
SESSION_TTL_DAYS = 7
STATE_TTL_MINUTES = 10
_ALGORITHM = "HS256"


def mint_session(user_id: uuid.UUID) -> str:
    """Issue a session token for a user."""
    return jwt.encode(
        {
            "sub": str(user_id),
            "typ": "session",
            "exp": datetime.now(UTC) + timedelta(days=SESSION_TTL_DAYS),
            "iat": datetime.now(UTC),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )


def verify_session(token: str | None) -> uuid.UUID | None:
    """Return the user id, or None for any invalid token.

    Returns None rather than raising: every failure mode (expired, tampered,
    malformed, wrong type) means the same thing to the caller — not logged in.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.session_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "session":
        return None
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None


def mint_state() -> str:
    """Issue a short-lived signed nonce for the OAuth `state` parameter.

    Signed rather than stored: it needs no server-side session store, and an
    attacker who cannot forge the signature cannot force a victim's browser to
    complete an authorization the attacker began (CSRF on the callback).
    """
    return jwt.encode(
        {
            "typ": "state",
            "jti": str(uuid.uuid4()),
            "exp": datetime.now(UTC) + timedelta(minutes=STATE_TTL_MINUTES),
        },
        settings.session_secret,
        algorithm=_ALGORITHM,
    )


def verify_state(state: str | None) -> bool:
    """True if `state` is a nonce this server minted and it has not expired."""
    if not state:
        return False
    try:
        payload = jwt.decode(state, settings.session_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return False
    return payload.get("typ") == "state"
