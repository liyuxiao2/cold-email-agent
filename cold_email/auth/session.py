"""Stateless session tokens and CSRF state nonces."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from cold_email.auth.constants import (
    ALGORITHM,
    SESSION_TTL_DAYS,
    STATE_TTL_MINUTES,
)
from cold_email.auth.constants import SESSION_COOKIE as SESSION_COOKIE  # re-exported
from cold_email.config import settings

logger = logging.getLogger(__name__)


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
        algorithm=ALGORITHM,
    )


def verify_session(token: str | None) -> uuid.UUID | None:
    """Return the user id, or None for any invalid token."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.session_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "session":
        return None
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None


def mint_state() -> str:
    """Issue a short-lived signed nonce for the OAuth `state` parameter."""
    return jwt.encode(
        {
            "typ": "state",
            "jti": str(uuid.uuid4()),
            "exp": datetime.now(UTC) + timedelta(minutes=STATE_TTL_MINUTES),
        },
        settings.session_secret,
        algorithm=ALGORITHM,
    )


def verify_state(state: str | None) -> bool:
    """True if `state` is a nonce this server minted and it has not expired."""
    if not state:
        return False
    try:
        payload = jwt.decode(state, settings.session_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return False
    return payload.get("typ") == "state"
