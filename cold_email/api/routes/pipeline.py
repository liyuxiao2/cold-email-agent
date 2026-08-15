import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.deps import get_current_user, require_admin
from cold_email.database import Company, Outreach, User, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

_COMPANY_STATUSES = ["found", "researched", "failed"]
_OUTREACH_STATUSES = ["queued", "drafted", "approved", "sent", "rejected", "failed"]


@router.get("/stats")
async def get_pipeline_stats(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """Return counts at both levels of the tenancy split.

    `companies` is grouped by research_status and is GLOBAL — every company in
    the pool, same answer for every caller. `outreach` is grouped by status
    and is filtered to the caller's own rows, consistent with every other
    outreach query.
    """
    company_stmt = select(Company.research_status, func.count(Company.id)).group_by(
        Company.research_status
    )
    company_counts = dict((await session.execute(company_stmt)).all())
    companies_stats = {status: company_counts.get(status, 0) for status in _COMPANY_STATUSES}
    companies_stats["total"] = sum(company_counts.values())

    outreach_stmt = (
        select(Outreach.status, func.count(Outreach.id))
        .where(Outreach.user_id == user.id)
        .group_by(Outreach.status)
    )
    outreach_counts = dict((await session.execute(outreach_stmt)).all())
    outreach_stats = {status: outreach_counts.get(status, 0) for status in _OUTREACH_STATUSES}
    outreach_stats["total"] = sum(outreach_counts.values())

    return {"companies": companies_stats, "outreach": outreach_stats}


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
    """Re-dispatch research for companies stuck in 'found' — discovered but
    never researched.

    Discovery only enqueues research for brand-new companies, so a company
    found while the worker was down (or whose research task was lost) stays
    orphaned in 'found' and is never retried. This requeues them through the
    research worker.
    """
    from cold_email.workers.research import research_task

    result = await session.execute(select(Company).where(Company.research_status == "found"))
    company_ids = [str(company.id) for company in result.scalars().all()]

    for company_id in company_ids:
        research_task.delay(company_id)

    logger.info(f"Requeued {len(company_ids)} 'found' companies for research")
    return {"success": True, "requeued": len(company_ids), "company_ids": company_ids}
