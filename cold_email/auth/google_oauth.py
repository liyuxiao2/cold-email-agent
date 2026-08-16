"""Google OAuth2 authorization-code flow — the only module that talks to Google."""

import logging
import urllib.parse
from dataclasses import dataclass

import httpx
import jwt

from cold_email.auth.constants import (
    GOOGLE_AUTHORIZE_URL,
    GOOGLE_SCOPES,
    GOOGLE_TOKEN_URL,
    TOKEN_TIMEOUT_SECONDS,
)
from cold_email.config import settings

logger = logging.getLogger(__name__)


class OAuthExchangeFailed(RuntimeError):
    """Google rejected the authorization code, or returned an unusable response."""


@dataclass(frozen=True)
class GoogleIdentity:
    """What a successful code exchange tells us about the user."""

    sub: str
    email: str
    name: str | None
    picture_url: str | None
    refresh_token: str | None


def build_authorize_url(state: str) -> str:
    """Build the consent-screen URL the browser is sent to."""
    params = {
        "client_id": settings.gmail_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> GoogleIdentity:
    """Exchange an authorization code for identity claims and a refresh token."""
    try:
        response = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.gmail_client_id,
                "client_secret": settings.gmail_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=TOKEN_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise OAuthExchangeFailed(f"Token endpoint unreachable: {exc}") from exc

    if response.status_code != 200:
        # Log the status and Google's error code, never the authorization code.
        logger.error(f"Google token exchange failed: {response.status_code} {response.text[:200]}")
        raise OAuthExchangeFailed(f"Google returned {response.status_code}")

    payload = response.json()
    id_token = payload.get("id_token")
    if not id_token:
        raise OAuthExchangeFailed("Google response contained no id_token")

    claims = jwt.decode(id_token, options={"verify_signature": False})

    email = claims.get("email")
    sub = claims.get("sub")
    if not email or not sub:
        raise OAuthExchangeFailed("id_token missing sub or email")

    if claims.get("email_verified") is not True:
        raise OAuthExchangeFailed("id_token email is not verified")

    return GoogleIdentity(
        sub=sub,
        email=email,
        name=claims.get("name"),
        picture_url=claims.get("picture"),
        refresh_token=payload.get("refresh_token"),
    )
