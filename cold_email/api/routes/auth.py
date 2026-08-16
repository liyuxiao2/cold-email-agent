"""Google Sign-In routes.

One consent flow yields identity and Gmail send capability together. The
callback is the only place a Google refresh token is ever written, and it is
encrypted before it touches the database.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.crypto import encrypt
from cold_email.auth.deps import get_current_user
from cold_email.auth.google_oauth import (
    GoogleIdentity,
    OAuthExchangeFailed,
    build_authorize_url,
    exchange_code,
)
from cold_email.auth.session import (
    SESSION_COOKIE,
    SESSION_TTL_DAYS,
    mint_session,
    mint_state,
    verify_state,
)
from cold_email.config import settings
from cold_email.database import User, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


async def upsert_user(session: AsyncSession, identity: GoogleIdentity) -> User:
    """Create or update the user behind a Google identity.

    Matched on google_sub first (stable forever), then by email to claim a row
    seeded before that person's first sign-in. The seeded row's `role` is
    preserved — silently demoting the only admin would lock discovery and
    research away from everyone.
    """
    user = (
        await session.execute(select(User).where(User.google_sub == identity.sub))
    ).scalar_one_or_none()

    if user is None:
        user = (
            await session.execute(select(User).where(User.email == identity.email))
        ).scalar_one_or_none()

    if user is None:
        user = User(email=identity.email)
        session.add(user)

    user.google_sub = identity.sub
    user.email = identity.email
    user.name = identity.name
    user.picture_url = identity.picture_url

    # Only overwrite when Google actually returned one. A re-login that omits
    # refresh_token must not wipe a working token.
    if identity.refresh_token:
        user.gmail_refresh_token_enc = encrypt(identity.refresh_token)
        user.gmail_sender_email = identity.email

    await session.commit()
    await session.refresh(user)
    return user


@router.get("/google/login")
async def google_login():
    """Return the consent-screen URL, carrying a signed CSRF state nonce."""
    return {"authorize_url": build_authorize_url(mint_state())}


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_async_session),
):
    """Complete the flow: verify state, exchange the code, set the session."""
    if not verify_state(state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state"
        )

    try:
        identity = exchange_code(code)
    except OAuthExchangeFailed as exc:
        logger.warning(f"OAuth exchange failed: {exc}")
        return RedirectResponse(
            url=f"{settings.frontend_url}/login?error=oauth_failed",
            status_code=status.HTTP_302_FOUND,
        )

    user = await upsert_user(session, identity)

    response = RedirectResponse(url=settings.frontend_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=mint_session(user.id),
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        httponly=True,  # unreadable by JavaScript, so an XSS cannot steal it
        secure=settings.cookie_secure,
        samesite="none" if settings.cookie_secure else "lax",
        path="/",
    )
    return response


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    """The caller's identity and connection state."""
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "picture_url": user.picture_url,
        "role": user.role,
        "gmail_connected": user.gmail_refresh_token_enc is not None,
    }


@router.post("/logout")
async def logout(response: Response, user: User = Depends(get_current_user)):
    """Clear the session cookie."""
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=settings.cookie_secure,
        samesite="none" if settings.cookie_secure else "lax",
    )
    return {"success": True}
