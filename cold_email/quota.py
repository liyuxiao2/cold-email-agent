"""Per-user monthly draft quota.

Counts outreach rows CREATED in the current UTC calendar month, not sends. The
LLM call is the cost and it happens at drafting, so a user who drafts 100 and
approves 3 has spent 100 units of the thing being rationed.

BYOK users bypass this entirely — they are spending their own limits.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.database import Outreach, User

logger = logging.getLogger(__name__)


def period_start(now: datetime | None = None) -> datetime:
    """Midnight UTC on the first of the current month.

    Calendar month in UTC rather than a rolling 30-day window: users reason
    about "this month", and a rolling window makes remaining quota drift
    unpredictably day to day.
    """
    now = now or datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


async def usage(session: AsyncSession, user: User) -> tuple[int, int]:
    """Return (used_this_period, limit)."""
    used = (
        await session.execute(
            select(func.count(Outreach.id)).where(
                Outreach.user_id == user.id,
                Outreach.created_at >= period_start(),
            )
        )
    ).scalar_one()
    return used, user.monthly_draft_quota


async def check(session: AsyncSession, user: User, requested: int) -> int:
    """How many of `requested` new outreach rows the user may create.

    Clamps rather than raising, so POST /api/outreach can create the allowed
    subset and report the rest as skipped — a user selecting 20 with 12 left
    should get 12 drafts and a clear note, not a 400 and nothing.
    """
    if user.llm_api_key_enc:
        return requested  # BYOK: their key, their limits

    used, limit = await usage(session, user)
    return max(0, min(requested, limit - used))
