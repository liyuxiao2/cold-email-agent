"""Google OAuth2 authorization-code flow — the only module that talks to Google.

One consent screen yields both identity (openid/email/profile) and send
capability (gmail.compose), so login and Gmail connection are a single step.

The existing Google Cloud OAuth client is reused: gmail_client_id and
gmail_client_secret identify this *application* and are required to refresh any
user's token. They are app-level, not per-user.
"""

import logging
import urllib.parse
from dataclasses import dataclass

import httpx
import jwt

from cold_email.config import settings

logger = logging.getLogger(__name__)

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (endpoint, not a secret)

GOOGLE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.compose",
]

TOKEN_TIMEOUT_SECONDS = 15


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
    """Build the consent-screen URL the browser is sent to.

    access_type=offline requests a refresh token; prompt=consent forces Google
    to return one even for a user who has consented before. Without it, only a
    user's first-ever authorization yields a refresh token, so a re-signup
    produces an account that cannot send.
    """
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

    # The id_token came over TLS straight from Google's token endpoint in
    # response to our client_secret, so its claims are trusted without
    # re-verifying the signature. (Signature verification would be required if
    # the token arrived from the client instead.)
    claims = jwt.decode(id_token, options={"verify_signature": False})

    email = claims.get("email")
    sub = claims.get("sub")
    if not email or not sub:
        raise OAuthExchangeFailed("id_token missing sub or email")

    # The canonical Sign-in-with-Google pitfall: an id_token can carry an
    # unverified email. This code uses email as an identity key (the
    # seeded-admin and signup-allowlist matches in upsert_user /
    # is_signup_allowed), so an unverified email must never be trusted.
    if claims.get("email_verified") is not True:
        raise OAuthExchangeFailed("id_token email is not verified")

    return GoogleIdentity(
        sub=sub,
        email=email,
        name=claims.get("name"),
        picture_url=claims.get("picture"),
        # Absent when the user has consented before — a send problem, not a
        # login problem. The caller surfaces it as gmail_connected: false.
        refresh_token=payload.get("refresh_token"),
    )
