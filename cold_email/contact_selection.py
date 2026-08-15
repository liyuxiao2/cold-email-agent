"""Choose which human at a company a user will email.

The problem: the company pool is fully shared, so without spreading, every user
emails the same founder_email and that founder receives N near-identical emails
from N senders. That reads as a spam farm.

The solution: pick the LEAST-GLOBALLY-CONTACTED eligible contact under a cap.

Why least-used and not random — random distribution is lumpy: with 6 contacts and
6 users, some address gets hit twice while another gets zero, which is the exact
outcome this exists to prevent. Least-used spreads evenly by construction.

Why that matters more than it sounds: this is a pure function over counts, so the
cap, the ordering, and the exhaustion case are directly assertable. A randomised
version needs seeding or mocking, and "distributes evenly" degrades from an
assertion to a statistical claim.
"""

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

CONTACT_CAP_DEFAULT = 3

# ORDER BY rationale, term by term:
#   use_count ASC   — spreading is the whole point, so it dominates
#   confidence DESC — among equally-used contacts, prefer the deliverable one
#   is_founder DESC — BELOW use_count deliberately: above it, volume
#                     re-concentrates on founders
#   contact_id ASC  — a TOTAL ordering. Without it Postgres may return either
#                     of two equal rows, making tests flaky in a way that
#                     looks like a selection bug.
_SELECT_CONTACT = text("""
    SELECT contact_id
    FROM available_contacts
    WHERE company_id = :company_id
      AND use_count < :cap
    ORDER BY use_count ASC, confidence DESC, is_founder DESC, contact_id ASC
    LIMIT 1
""")


async def select_contact(
    session: AsyncSession, company_id: uuid.UUID, cap: int = CONTACT_CAP_DEFAULT
) -> uuid.UUID | None:
    """The least-contacted eligible contact at a company, or None if exhausted.

    Reads the available_contacts view, which already filters to eligible
    contacts and computes use_count across ALL users. A per-caller count would
    let ten users each email the same founder exactly once.

    None means every eligible contact has hit the cap — the company should drop
    out of the pool.
    """
    result = await session.execute(_SELECT_CONTACT, {"company_id": company_id, "cap": cap})
    contact_id = result.scalar_one_or_none()

    if contact_id is None:
        logger.info(f"Company {company_id} has no available contact under cap {cap}")
    return contact_id
