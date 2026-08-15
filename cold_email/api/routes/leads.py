import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cold_email.auth.deps import get_current_user
from cold_email.database import Lead, User, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["leads"])


class RejectRequest(BaseModel):
    notes: str = ""


# Login-gated in Stack 1a; user-SCOPED in Stack 1b once `outreach` exists.
# Until then every authenticated user sees the same leads.
@router.get("/drafts")
async def get_draft_review_queue(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """Return all leads currently with status='drafted', including draft and research."""
    stmt = (
        select(Lead)
        .where(Lead.status == "drafted")
        .options(selectinload(Lead.drafts), selectinload(Lead.research))
        .order_by(Lead.created_at.desc())
    )
    result = await session.execute(stmt)
    leads = result.scalars().all()

    items = []
    for lead in leads:
        # Newest draft by created_at — consistent with the pending_sends view.
        # (version is vestigial: commit_draft leaves it =1, so keying on version
        # returns a stale row after a regenerate.)
        latest_draft = max(lead.drafts, key=lambda d: d.created_at, default=None)
        latest_research = (
            max(lead.research, key=lambda r: r.created_at, default=None) if lead.research else None
        )

        items.append(
            {
                "id": str(lead.id),
                "company_name": lead.company_name,
                "founder_name": lead.founder_name,
                "founder_email": lead.founder_email,
                "company_url": lead.company_url,
                "linkedin_url": lead.linkedin_url,
                "funding_stage": lead.funding_stage,
                "headcount": lead.headcount,
                "status": lead.status,
                "created_at": lead.created_at.isoformat() if lead.created_at else None,
                "draft": {
                    "id": str(latest_draft.id),
                    "subject_line": latest_draft.subject_line,
                    "body": latest_draft.body,
                    "version": latest_draft.version,
                    "gmail_draft_id": latest_draft.gmail_draft_id,
                    "created_at": latest_draft.created_at.isoformat()
                    if latest_draft.created_at
                    else None,
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
        )

    return items


@router.get("")
async def list_leads(
    status: str | None = Query(
        None,
        description="Filter by status (found, researched, drafted, approved, sent, rejected, failed)",
    ),
    search: str | None = Query(None, description="Search company or founder name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """List leads with pagination, filtering, and search."""
    stmt = select(Lead).options(selectinload(Lead.drafts), selectinload(Lead.research))

    if status:
        stmt = stmt.where(Lead.status == status)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where((Lead.company_name.ilike(pattern)) | (Lead.founder_name.ilike(pattern)))

    stmt = stmt.order_by(Lead.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    leads = result.scalars().all()

    # Total count matching query
    count_stmt = select(func.count(Lead.id))
    if status:
        count_stmt = count_stmt.where(Lead.status == status)
    if search:
        pattern = f"%{search}%"
        count_stmt = count_stmt.where(
            (Lead.company_name.ilike(pattern)) | (Lead.founder_name.ilike(pattern))
        )
    total_matching = (await session.execute(count_stmt)).scalar_one()

    items = []
    for lead in leads:
        # Newest draft by created_at — consistent with the pending_sends view.
        # (version is vestigial: commit_draft leaves it =1, so keying on version
        # returns a stale row after a regenerate.)
        latest_draft = max(lead.drafts, key=lambda d: d.created_at, default=None)
        latest_research = (
            max(lead.research, key=lambda r: r.created_at, default=None) if lead.research else None
        )

        items.append(
            {
                "id": str(lead.id),
                "company_name": lead.company_name,
                "founder_name": lead.founder_name,
                "founder_email": lead.founder_email,
                "company_url": lead.company_url,
                "linkedin_url": lead.linkedin_url,
                "funding_stage": lead.funding_stage,
                "headcount": lead.headcount,
                "status": lead.status,
                "error_msg": lead.error_msg,
                "created_at": lead.created_at.isoformat() if lead.created_at else None,
                "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
                "draft": {
                    "id": str(latest_draft.id),
                    "subject_line": latest_draft.subject_line,
                    "body": latest_draft.body,
                    "version": latest_draft.version,
                    "gmail_draft_id": latest_draft.gmail_draft_id,
                }
                if latest_draft
                else None,
                "research": {
                    "hook": latest_research.hook if latest_research else None,
                }
                if latest_research
                else None,
            }
        )

    return {
        "items": items,
        "total": total_matching,
        "limit": limit,
        "offset": offset,
    }


@router.post("/{lead_id}/approve")
async def approve_lead_api(
    lead_id: str,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """Approve a drafted lead and dispatch logistics task."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead.status = "approved"
    await session.commit()

    try:
        from cold_email.workers.logistics import logistics_task

        task = logistics_task.delay(lead_id)
        task_id = task.id
    except Exception as e:
        logger.warning(f"Could not dispatch logistics_task to Celery broker: {e}")
        task_id = None

    return {
        "success": True,
        "lead_id": lead_id,
        "status": "approved",
        "task_id": task_id,
    }


@router.post("/{lead_id}/reject")
async def reject_lead_api(
    lead_id: str,
    payload: RejectRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """Reject a drafted lead with optional notes."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead.status = "rejected"
    if payload and payload.notes:
        lead.error_msg = payload.notes
    await session.commit()

    return {
        "success": True,
        "lead_id": lead_id,
        "status": "rejected",
    }


@router.post("/{lead_id}/regenerate")
async def regenerate_lead_api(
    lead_id: str,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """Reset lead to researched and trigger drafting sweep."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead.status = "researched"
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
        "lead_id": lead_id,
        "status": "researched",
        "task_id": task_id,
    }
