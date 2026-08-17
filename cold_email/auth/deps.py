"""FastAPI dependencies — the only auth surface routes should import."""

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.session import SESSION_COOKIE, verify_session
from cold_email.database import User, get_async_session


async def get_current_user(
    ce_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Resolve the caller, or 401."""
    user_id = verify_session(ce_session)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Resolve the caller and require the admin role, else 403."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user
