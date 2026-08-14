import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cold_email.database import DeadLetter, Lead, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["pipeline"])


class RejectRequest(BaseModel):
    notes: str = ""


def _latest(items):
    """Newest row by created_at, or None.

    version is vestigial (commit_draft leaves it =1), so keying on created_at is
    what stays correct after a regenerate — consistent with the pending_sends view.
    """
    return max(items, key=lambda x: x.created_at, default=None) if items else None


def _serialize_lead(lead, *, include_meta: bool = False) -> dict:
    """Serialize a Lead + its latest draft/research to the API's JSON shape.

    include_meta adds error_msg/updated_at (the /leads explorer wants them, the
    review queue doesn't). The frontend LeadItem type is a superset of both, so
    the two endpoints share one serializer.
    """
    latest_draft = _latest(lead.drafts)
    latest_research = _latest(lead.research)
    item = {
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
            "created_at": latest_draft.created_at.isoformat() if latest_draft.created_at else None,
        }
        if latest_draft
        else None,
        "research": {
            "hook": latest_research.hook,
            "tech_stack": latest_research.tech_stack,
            "recent_news": latest_research.recent_news,
        }
        if latest_research
        else None,
    }
    if include_meta:
        item["error_msg"] = lead.error_msg
        item["updated_at"] = lead.updated_at.isoformat() if lead.updated_at else None
    return item


def _apply_lead_filters(stmt, status: str | None, search: str | None):
    """Apply the shared status/search WHERE clauses to a leads query.

    Used for both the row query and its COUNT so the two can't drift.
    """
    if status:
        stmt = stmt.where(Lead.status == status)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where((Lead.company_name.ilike(pattern)) | (Lead.founder_name.ilike(pattern)))
    return stmt


@router.get("/health")
async def health_check(session: AsyncSession = Depends(get_async_session)):
    """Health check verifying API and Database connectivity."""
    try:
        await session.execute(select(func.count()).select_from(Lead))
        db_status = "connected"
    except Exception as e:
        logger.error(f"Health check DB error: {e}")
        db_status = f"unhealthy: {e}"

    return {"status": "ok", "database": db_status}


@router.get("/pipeline/stats")
async def get_pipeline_stats(session: AsyncSession = Depends(get_async_session)):
    """Return count of leads grouped by status."""
    stmt = select(Lead.status, func.count(Lead.id)).group_by(Lead.status)
    result = await session.execute(stmt)
    counts = dict(result.all())

    all_statuses = ["found", "researched", "drafted", "approved", "sent", "rejected", "failed"]
    stats = {status: counts.get(status, 0) for status in all_statuses}
    stats["total"] = sum(counts.values())

    return stats


@router.get("/leads/drafts")
async def get_draft_review_queue(session: AsyncSession = Depends(get_async_session)):
    """Return all leads currently with status='drafted', including draft and research."""
    stmt = (
        select(Lead)
        .where(Lead.status == "drafted")
        .options(selectinload(Lead.drafts), selectinload(Lead.research))
        .order_by(Lead.created_at.desc())
    )
    result = await session.execute(stmt)
    leads = result.scalars().all()

    return [_serialize_lead(lead) for lead in leads]


@router.get("/leads")
async def list_leads(
    status: str | None = Query(
        None,
        description="Filter by status (found, researched, drafted, approved, sent, rejected, failed)",
    ),
    search: str | None = Query(None, description="Search company or founder name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_session),
):
    """List leads with pagination, filtering, and search."""
    stmt = (
        _apply_lead_filters(
            select(Lead).options(selectinload(Lead.drafts), selectinload(Lead.research)),
            status,
            search,
        )
        .order_by(Lead.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    leads = result.scalars().all()

    count_stmt = _apply_lead_filters(select(func.count(Lead.id)), status, search)
    total_matching = (await session.execute(count_stmt)).scalar_one()
    return {
        "items": [_serialize_lead(lead, include_meta=True) for lead in leads],
        "total": total_matching,
        "limit": limit,
        "offset": offset,
    }


@router.post("/leads/{lead_id}/approve")
async def approve_lead_api(
    lead_id: str,
    session: AsyncSession = Depends(get_async_session),
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


@router.post("/leads/{lead_id}/reject")
async def reject_lead_api(
    lead_id: str,
    payload: RejectRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
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


@router.post("/leads/{lead_id}/regenerate")
async def regenerate_lead_api(
    lead_id: str,
    session: AsyncSession = Depends(get_async_session),
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


def _queue_pipeline_task(task, label: str) -> dict:
    """Enqueue a /pipeline/* Celery task, surfacing a broker failure as a 500."""
    try:
        result = task.delay()
        return {"success": True, "message": f"{label} task queued", "task_id": result.id}
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Failed to queue {label.lower()} task: {e}\n{tb}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue {label.lower()} task: {e} | Traceback: {tb}",
        ) from e


@router.post("/pipeline/discovery")
async def trigger_discovery_api():
    """Manually trigger a discovery run."""
    from cold_email.workers.discovery import discovery_task

    return _queue_pipeline_task(discovery_task, "Discovery")


@router.post("/pipeline/drafting")
async def trigger_drafting_api():
    """Manually trigger a drafting batch sweep."""
    from cold_email.workers.drafting import drafting_task

    return _queue_pipeline_task(drafting_task, "Drafting")


@router.post("/pipeline/research")
async def trigger_research_api(
    session: AsyncSession = Depends(get_async_session),
):
    """Re-dispatch research for leads stuck in 'found' — discovered but never researched.

    Discovery only enqueues research for brand-new leads, so a lead found while
    the worker was down (or whose research task was lost) stays orphaned in
    'found' and is never retried. This requeues them through the research worker.
    """
    from cold_email.workers.research import research_task

    result = await session.execute(select(Lead).where(Lead.status == "found"))
    lead_ids = [str(lead.id) for lead in result.scalars().all()]

    for lead_id in lead_ids:
        research_task.delay(lead_id)

    logger.info(f"Requeued {len(lead_ids)} 'found' leads for research")
    return {"success": True, "requeued": len(lead_ids), "lead_ids": lead_ids}


# Maps a dead-letter row's stage back to (reset-status, re-dispatch). The lead is
# reset to the stage's input state so re-dispatch re-runs that stage cleanly.
_DLQ_STAGE_RESET = {
    "research": "found",
    "drafting": "researched",
    "logistics": "approved",
}


@router.get("/dlq")
async def list_dead_letter(session: AsyncSession = Depends(get_async_session)):
    """List dead-lettered (terminally-failed) tasks awaiting retry."""
    stmt = (
        select(DeadLetter, Lead.company_name)
        .join(Lead, DeadLetter.lead_id == Lead.id, isouter=True)
        .order_by(DeadLetter.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return {
        "count": len(rows),
        "items": [
            {
                "id": str(dl.id),
                "lead_id": str(dl.lead_id),
                "company_name": company_name,
                "task_name": dl.task_name,
                "stage": dl.stage,
                "error_msg": dl.error_msg,
                "retry_count": dl.retry_count,
                "created_at": dl.created_at.isoformat() if dl.created_at else None,
            }
            for dl, company_name in rows
        ],
    }


@router.post("/dlq/retry")
async def retry_dead_letter(
    stage: str | None = Query(
        None, description="Only retry this stage (research/drafting/logistics)"
    ),
    session: AsyncSession = Depends(get_async_session),
):
    """Re-dispatch dead-lettered tasks: reset each lead to its stage's input
    state, re-enqueue the worker, and clear the row. A task that fails again is
    written back to the DLQ by handle_terminal_failure, so the queue self-cleans.
    """
    from cold_email.workers.drafting import drafting_task
    from cold_email.workers.logistics import logistics_task
    from cold_email.workers.research import research_task

    stmt = select(DeadLetter)
    if stage:
        stmt = stmt.where(DeadLetter.stage == stage)
    rows = (await session.execute(stmt)).scalars().all()

    retried = 0
    for dl in rows:
        stage_val, lead_id = dl.stage, str(dl.lead_id)
        reset_status = _DLQ_STAGE_RESET.get(stage_val)
        if reset_status is None:
            logger.warning(f"DLQ row {dl.id} has unknown stage {stage_val!r}; skipping")
            continue
        lead = await session.get(Lead, dl.lead_id)
        if lead is not None:
            lead.status = reset_status
            lead.error_msg = None
        await session.delete(dl)
        if stage_val == "research":
            research_task.delay(lead_id)
        elif stage_val == "drafting":
            drafting_task.delay()
        elif stage_val == "logistics":
            logistics_task.delay(lead_id)
        retried += 1

    await session.commit()
    logger.info(f"Retried {retried} dead-lettered task(s)" + (f" (stage={stage})" if stage else ""))
    return {"success": True, "retried": retried}
