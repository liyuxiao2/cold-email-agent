import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.deps import get_current_user
from cold_email.database import DeadLetter, Lead, User, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dlq", tags=["dlq"])

# Maps a dead-letter row's stage back to (reset-status, re-dispatch). The lead is
# reset to the stage's input state so re-dispatch re-runs that stage cleanly.
_DLQ_STAGE_RESET = {
    "research": "found",
    "drafting": "researched",
    "logistics": "approved",
}


@router.get("")
async def list_dead_letter(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
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


@router.post("/retry")
async def retry_dead_letter(
    stage: str | None = Query(
        None, description="Only retry this stage (research/drafting/logistics)"
    ),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
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
