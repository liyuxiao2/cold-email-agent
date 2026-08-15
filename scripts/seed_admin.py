"""Idempotently ensure ADMIN_EMAIL exists with role='admin'.

Run on every boot by start.sh. The seeded row has a NULL google_sub; the OAuth
callback claims it by email on that person's first sign-in and preserves the
admin role.

Without this, a fresh deployment has no admin, so nobody can trigger discovery
or research and the pool never fills.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.config import settings
from cold_email.database import ROLE_ADMIN, AsyncSessionLocal, User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def seed_admin(session: AsyncSession) -> None:
    """Create or promote the configured admin. Safe to call repeatedly."""
    email = settings.admin_email
    if not email:
        logger.info("ADMIN_EMAIL unset; skipping admin seed")
        return

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    if user is None:
        session.add(User(email=email, role=ROLE_ADMIN))
        logger.info(f"Seeded admin user {email}")
    elif user.role != ROLE_ADMIN:
        user.role = ROLE_ADMIN
        logger.info(f"Promoted {email} to admin")
    else:
        logger.info(f"Admin {email} already present")

    await session.commit()


async def _main() -> None:
    async with AsyncSessionLocal() as session:
        await seed_admin(session)


if __name__ == "__main__":
    asyncio.run(_main())
