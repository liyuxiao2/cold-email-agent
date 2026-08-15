"""Per-user outreach routes (replaces leads.py).

Every query filters on Outreach.user_id. Lookups use a WHERE on both id and
user_id rather than session.get(), so another user's row is indistinguishable
from a nonexistent one — a 403 would confirm the id exists and turn an
authorization check into an existence oracle.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cold_email.auth.deps import get_current_user
from cold_email.database import (
    OUTREACH_APPROVED,
    OUTREACH_DRAFTED,
    OUTREACH_QUEUED,
    OUTREACH_REJECTED,
    Company,
    Outreach,
    User,
    get_async_session,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/outreach", tags=["outreach"])


class RejectRequest(BaseModel):
    notes: str = ""


async def _own_outreach(session: AsyncSession, outreach_id: str, user: User) -> Outreach:
    """Fetch an outreach row the caller owns, or 404.

    Filtering by user_id in the QUERY (rather than fetching then comparing)
    makes correct behaviour fall out of the query shape instead of depending on
    a remembered convention at each call site.
    """
    result = await session.execute(
        select(Outreach)
        .where(Outreach.id == outreach_id, Outreach.user_id == user.id)
        .options(selectinload(Outreach.drafts), selectinload(Outreach.company))
    )
    outreach = result.scalar_one_or_none()
    if outreach is None:
        raise HTTPException(status_code=404, detail="Outreach not found")
    return outreach


def _serialize_outreach(outreach: Outreach) -> dict:
    """Shape one outreach row for the API: nested `company` and `contact`
    rather than flat `founder_*` fields, so a caller can tell which human is
    being emailed, not just which company.
    """
    company = outreach.company
    contact = outreach.contact
    latest_draft = max(outreach.drafts, key=lambda d: d.created_at, default=None)
    latest_research = (
        max(company.research, key=lambda r: r.created_at, default=None)
        if company is not None and company.research
        else None
    )

    return {
        "outreach_id": str(outreach.id),
        "status": outreach.status,
        "error_msg": outreach.error_msg,
        "created_at": outreach.created_at.isoformat() if outreach.created_at else None,
        "updated_at": outreach.updated_at.isoformat() if outreach.updated_at else None,
        "company": {
            "id": str(company.id),
            "company_name": company.company_name,
            "company_url": company.company_url,
            "linkedin_url": company.linkedin_url,
            "funding_stage": company.funding_stage,
            "headcount": company.headcount,
        }
        if company is not None
        else None,
        "contact": {
            "first_name": contact.first_name,
            "position": contact.position,
            "email": contact.email,
        }
        if contact is not None
        else None,
        "draft": {
            "id": str(latest_draft.id),
            "subject_line": latest_draft.subject_line,
            "body": latest_draft.body,
            "version": latest_draft.version,
            "gmail_draft_id": latest_draft.gmail_draft_id,
            "created_at": latest_draft.created_at.isoformat() if latest_draft.created_at else None,
        }
        if latest_draft
        else None,
        "research": {
            "hook": latest_research.hook if latest_research else None,
            "tech_stack": latest_research.tech_stack if latest_research else None,
            "recent_news": latest_research.recent_news if latest_research else None,
        }
        if latest_research
        else None,
    }


@router.get("/drafts")
async def get_draft_review_queue(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """Return all of THIS user's outreach rows currently drafted, including
    draft and research."""
    stmt = (
        select(Outreach)
        .where(Outreach.user_id == user.id, Outreach.status == OUTREACH_DRAFTED)
        .options(
            selectinload(Outreach.drafts),
            selectinload(Outreach.contact),
            selectinload(Outreach.company).selectinload(Company.research),
        )
        .order_by(Outreach.created_at.desc())
    )
    result = await session.execute(stmt)
    outreach_rows = result.scalars().all()

    return [_serialize_outreach(o) for o in outreach_rows]


@router.get("")
async def list_outreach(
    status: str | None = Query(
        None,
        description="Filter by status (queued, drafted, approved, sent, rejected, failed)",
    ),
    search: str | None = Query(None, description="Search company or founder name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """List THIS user's outreach rows with pagination, filtering, and search."""
    stmt = (
        select(Outreach)
        .join(Outreach.company)
        .where(Outreach.user_id == user.id)
        .options(
            selectinload(Outreach.drafts),
            selectinload(Outreach.contact),
            selectinload(Outreach.company).selectinload(Company.research),
        )
    )
    count_stmt = (
        select(func.count(Outreach.id)).join(Outreach.company).where(Outreach.user_id == user.id)
    )

    if status:
        stmt = stmt.where(Outreach.status == status)
        count_stmt = count_stmt.where(Outreach.status == status)
    if search:
        pattern = f"%{search}%"
        search_clause = (Company.company_name.ilike(pattern)) | (
            Company.founder_name.ilike(pattern)
        )
        stmt = stmt.where(search_clause)
        count_stmt = count_stmt.where(search_clause)

    stmt = stmt.order_by(Outreach.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    outreach_rows = result.scalars().all()

    total_matching = (await session.execute(count_stmt)).scalar_one()

    return {
        "items": [_serialize_outreach(o) for o in outreach_rows],
        "total": total_matching,
        "limit": limit,
        "offset": offset,
    }


@router.post("/{outreach_id}/approve")
async def approve_outreach_api(
    outreach_id: str,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """Approve THIS user's drafted outreach row and dispatch logistics task."""
    outreach = await _own_outreach(session, outreach_id, user)

    outreach.status = OUTREACH_APPROVED
    await session.commit()

    try:
        from cold_email.workers.logistics import logistics_task

        task = logistics_task.delay(outreach_id)
        task_id = task.id
    except Exception as e:
        logger.warning(f"Could not dispatch logistics_task to Celery broker: {e}")
        task_id = None

    return {
        "success": True,
        "outreach_id": outreach_id,
        "status": OUTREACH_APPROVED,
        "task_id": task_id,
    }


@router.post("/{outreach_id}/reject")
async def reject_outreach_api(
    outreach_id: str,
    payload: RejectRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """Reject THIS user's drafted outreach row with optional notes."""
    outreach = await _own_outreach(session, outreach_id, user)

    outreach.status = OUTREACH_REJECTED
    if payload and payload.notes:
        outreach.error_msg = payload.notes
    await session.commit()

    return {
        "success": True,
        "outreach_id": outreach_id,
        "status": OUTREACH_REJECTED,
    }


@router.post("/{outreach_id}/regenerate")
async def regenerate_outreach_api(
    outreach_id: str,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """Reset THIS user's outreach row to queued and trigger the drafting sweep."""
    outreach = await _own_outreach(session, outreach_id, user)

    outreach.status = OUTREACH_QUEUED
    await session.commit()

    try:
        from cold_email.workers.drafting import drafting_task

        task = drafting_task.delay()
        task_id = task.id
    except Exception as e:
        logger.warning(f"Could not dispatch drafting_task to Celery broker: {e}")
        task_id = None

    return {
        "success": True,
        "outreach_id": outreach_id,
        "status": OUTREACH_QUEUED,
        "task_id": task_id,
    }
