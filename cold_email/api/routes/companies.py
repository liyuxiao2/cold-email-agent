"""Read-only routes over the global company pool.

Companies and their research are GLOBAL — the same rows are visible to every
authenticated user — unlike outreach.py, which is per-user and filters on
Outreach.user_id. This is the admin-populated pool a user later selects from
(Stack 3 adds selection); for now it is a read-only explorer.
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cold_email.auth.deps import get_current_user
from cold_email.database import Company, User, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["companies"])


def _serialize_company(company: Company) -> dict:
    latest_research = (
        max(company.research, key=lambda r: r.created_at, default=None)
        if company.research
        else None
    )
    return {
        "id": str(company.id),
        "company_name": company.company_name,
        "company_url": company.company_url,
        "linkedin_url": company.linkedin_url,
        "founder_name": company.founder_name,
        "funding_stage": company.funding_stage,
        "headcount": company.headcount,
        "industry": company.industry,
        "research_status": company.research_status,
        "error_msg": company.error_msg,
        "created_at": company.created_at.isoformat() if company.created_at else None,
        "updated_at": company.updated_at.isoformat() if company.updated_at else None,
        "research": {
            "hook": latest_research.hook,
            "tech_stack": latest_research.tech_stack,
            "recent_news": latest_research.recent_news,
        }
        if latest_research
        else None,
    }


@router.get("")
async def list_companies(
    status: str | None = Query(
        None, description="Filter by research_status (found, researched, failed)"
    ),
    search: str | None = Query(None, description="Search company or founder name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """List the global company pool. Every authenticated user sees the same
    rows — there is no per-user filter here, unlike /api/outreach."""
    stmt = select(Company).options(selectinload(Company.research))
    count_stmt = select(func.count(Company.id))

    if status:
        stmt = stmt.where(Company.research_status == status)
        count_stmt = count_stmt.where(Company.research_status == status)
    if search:
        pattern = f"%{search}%"
        clause = (Company.company_name.ilike(pattern)) | (Company.founder_name.ilike(pattern))
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)

    stmt = stmt.order_by(Company.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    companies = result.scalars().all()

    total_matching = (await session.execute(count_stmt)).scalar_one()

    return {
        "items": [_serialize_company(c) for c in companies],
        "total": total_matching,
        "limit": limit,
        "offset": offset,
    }
