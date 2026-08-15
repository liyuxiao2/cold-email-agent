"""Global company pool browsing.

Contact EMAIL ADDRESSES are never returned. The pool is the product's inventory;
exposing a scrapeable list of Hunter-verified founder addresses to every signup
turns the app into a lead-list leak. An address is revealed only inside the
user's own draft, after a contact has been assigned to them (see outreach.py).

Companies and their research are otherwise GLOBAL — the same rows are visible
to every authenticated user, unlike outreach.py, which is per-user — with one
exception: a company already targeted by the CALLER drops out of THEIR pool
(but stays visible to everyone else), and a company whose eligible contacts
have all hit the global per-contact cap drops out of everyone's pool.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.deps import get_current_user
from cold_email.config import settings
from cold_email.database import User, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["companies"])

# NOT EXISTS scoped to :user_id — the tenancy-sensitive clause. A LEFT JOIN on
# outreach without the user predicate would leak that someone else is working a
# company AND wrongly hide it from everyone.
_POOL_SQL = """
SELECT
    c.id, c.company_name, c.company_url, c.linkedin_url, c.founder_name,
    c.funding_stage, c.headcount, c.industry, c.created_at,
    r.hook, r.tech_stack, r.recent_news,
    avail.contact_count,
    avail.has_founder
FROM companies c
JOIN LATERAL (
    SELECT COUNT(*) AS contact_count, bool_or(ac.is_founder) AS has_founder
    FROM available_contacts ac
    WHERE ac.company_id = c.id AND ac.use_count < :cap
) avail ON TRUE
LEFT JOIN LATERAL (
    SELECT hook, tech_stack, recent_news FROM research
    WHERE company_id = c.id ORDER BY created_at DESC LIMIT 1
) r ON TRUE
WHERE c.research_status = 'researched'
  AND avail.contact_count > 0
  AND NOT EXISTS (
      SELECT 1 FROM outreach o WHERE o.company_id = c.id AND o.user_id = :user_id
  )
"""


@router.get("")
async def list_pool(
    industry: str | None = Query(None),
    funding_stage: str | None = Query(None),
    headcount_min: int | None = Query(None, ge=0),
    headcount_max: int | None = Query(None, ge=0),
    search: str | None = Query(None),
    has_founder_contact: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Companies the caller can still target."""
    sql = _POOL_SQL
    params: dict = {"cap": settings.contact_cap, "user_id": user.id}

    if industry:
        sql += " AND c.industry = :industry"
        params["industry"] = industry
    if funding_stage:
        sql += " AND c.funding_stage = :funding_stage"
        params["funding_stage"] = funding_stage
    if headcount_min is not None:
        sql += " AND c.headcount >= :headcount_min"
        params["headcount_min"] = headcount_min
    if headcount_max is not None:
        sql += " AND c.headcount <= :headcount_max"
        params["headcount_max"] = headcount_max
    if search:
        sql += " AND c.company_name ILIKE :search"
        params["search"] = f"%{search}%"
    if has_founder_contact:
        sql += " AND avail.has_founder"

    # Every fragment appended to `sql` above is a static, code-controlled
    # string (column/keyword text); the actual values ride in `params` as
    # bind parameters. Nothing here is attacker-controlled.
    count_sql = f"SELECT COUNT(*) FROM ({sql}) sub"  # noqa: S608
    total = (await session.execute(text(count_sql), params)).scalar_one()

    sql += " ORDER BY c.created_at DESC LIMIT :limit OFFSET :offset"
    params |= {"limit": limit, "offset": offset}
    rows = (await session.execute(text(sql), params)).mappings().all()

    return {
        "items": [
            {
                "id": str(row["id"]),
                "company_name": row["company_name"],
                "company_url": row["company_url"],
                "linkedin_url": row["linkedin_url"],
                "founder_name": row["founder_name"],
                "funding_stage": row["funding_stage"],
                "headcount": row["headcount"],
                "industry": row["industry"],
                # Lets the UI show "3 contacts available" with no second request.
                "contact_count": row["contact_count"],
                "has_founder_contact": row["has_founder"],
                "research": {"hook": row["hook"], "tech_stack": row["tech_stack"]},
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{company_id}")
async def get_company(
    company_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """One company with full research and contact SUMMARIES — no addresses."""
    row = (
        (
            await session.execute(
                text("""
                SELECT c.*, r.hook, r.tech_stack, r.recent_news
                FROM companies c
                LEFT JOIN LATERAL (
                    SELECT hook, tech_stack, recent_news FROM research
                    WHERE company_id = c.id ORDER BY created_at DESC LIMIT 1
                ) r ON TRUE
                WHERE c.id = :id AND c.research_status = 'researched'
                """),
                {"id": company_id},
            )
        )
        .mappings()
        .one_or_none()
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Company not found")

    contacts = (
        (
            await session.execute(
                text("""
                SELECT ct.first_name, ct.position, ct.is_founder
                FROM company_contacts ct
                JOIN available_contacts ac ON ac.contact_id = ct.id
                WHERE ct.company_id = :id AND ac.use_count < :cap
                ORDER BY ac.use_count, ct.confidence DESC
                """),
                {"id": company_id, "cap": settings.contact_cap},
            )
        )
        .mappings()
        .all()
    )

    return {
        "id": str(row["id"]),
        "company_name": row["company_name"],
        "company_url": row["company_url"],
        "linkedin_url": row["linkedin_url"],
        "founder_name": row["founder_name"],
        "funding_stage": row["funding_stage"],
        "headcount": row["headcount"],
        "industry": row["industry"],
        "research": {
            "hook": row["hook"],
            "tech_stack": row["tech_stack"],
            "recent_news": row["recent_news"],
        },
        # first_name / position / is_founder only. No email, deliberately.
        "contacts": [dict(c) for c in contacts],
    }
