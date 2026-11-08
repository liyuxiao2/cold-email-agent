"""FastAPI dependencies — the only auth surface routes should import.

One dependency expresses the whole policy:
  * get_current_user — authenticated, else 401

Routes never parse a JWT or read a cookie themselves, so the session format is
free to change without touching a single route.
"""

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.session import SESSION_COOKIE, verify_session
from cold_email.database import User, get_async_session


async def get_current_user(
    ce_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Resolve the caller, or 401.

    Every invalid-token case collapses to the same answer — not logged in —
    so there is no need to distinguish expired from tampered from absent. A
    session whose user row no longer exists is also 401, not a 500.
    """
    user_id = verify_session(ce_session)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    return user


require_admin = get_current_user
