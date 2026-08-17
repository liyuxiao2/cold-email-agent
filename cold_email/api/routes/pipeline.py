import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.deps import get_current_user, require_admin
from cold_email.database import Lead, User, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/stats")
async def get_pipeline_stats(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """Return count of leads grouped by status."""
    stmt = select(Lead.status, func.count(Lead.id)).group_by(Lead.status)
    result = await session.execute(stmt)
    counts = dict(result.all())

    all_statuses = ["found", "researched", "drafted", "approved", "sent", "rejected", "failed"]
    stats = {status: counts.get(status, 0) for status in all_statuses}
    stats["total"] = sum(counts.values())

    return stats


@router.post("/discovery")
async def trigger_discovery_api(admin: User = Depends(require_admin)):
    """Manually trigger a discovery run."""
    try:
        from cold_email.workers.discovery import discovery_task

        task = discovery_task.delay()
        return {"success": True, "message": "Discovery task queued", "task_id": task.id}
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Failed to queue discovery task: {e}\n{tb}")
        raise HTTPException(
            status_code=500, detail=f"Failed to queue discovery task: {e} | Traceback: {tb}"
        ) from e


@router.post("/drafting")
async def trigger_drafting_api(admin: User = Depends(require_admin)):
    """Manually trigger a drafting batch sweep."""
    try:
        from cold_email.workers.drafting import drafting_task

        task = drafting_task.delay()
        return {"success": True, "message": "Drafting task queued", "task_id": task.id}
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Failed to queue drafting task: {e}\n{tb}")
        raise HTTPException(
            status_code=500, detail=f"Failed to queue drafting task: {e} | Traceback: {tb}"
        ) from e


@router.post("/research")
async def trigger_research_api(
    session: AsyncSession = Depends(get_async_session),
    admin: User = Depends(require_admin),
):
    """Re-dispatch research for leads stuck in 'found' — discovered but never researched."""
    from cold_email.workers.research import research_task

    result = await session.execute(select(Lead).where(Lead.status == "found"))
    lead_ids = [str(lead.id) for lead in result.scalars().all()]

    for lead_id in lead_ids:
        research_task.delay(lead_id)

    logger.info(f"Requeued {len(lead_ids)} 'found' leads for research")
    return {"success": True, "requeued": len(lead_ids), "lead_ids": lead_ids}
