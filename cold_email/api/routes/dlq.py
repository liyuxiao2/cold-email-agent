import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from cold_email.auth.deps import get_current_user, require_admin
from cold_email.database import (
    OUTREACH_APPROVED,
    OUTREACH_QUEUED,
    RESEARCH_FOUND,
    Company,
    DeadLetter,
    Outreach,
    User,
    get_async_session,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dlq", tags=["dlq"])

# Maps a dead-letter row's stage back to (reset-status, re-dispatch). The
# entity is reset to the stage's input state so re-dispatch re-runs that stage
# cleanly. research is a COMPANY-level reset; drafting/logistics are
# OUTREACH-level, matching fail_company vs fail_outreach.
_DLQ_STAGE_RESET = {
    "research": RESEARCH_FOUND,
    "drafting": OUTREACH_QUEUED,
    "logistics": OUTREACH_APPROVED,
}


@router.get("")
async def list_dead_letter(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
):
    """List dead-lettered (terminally-failed) tasks awaiting retry.

    A row's company name comes via EITHER nullable FK: company_id directly for
    a research failure, or outreach_id -> outreach.company_id for a
    drafting/logistics failure. The two are joined with separate aliases and
    coalesced, since exactly one of the two FKs is ever set (see the
    dead_letter_one_level CHECK).
    """
    outreach_company = aliased(Company)
    stmt = (
        select(DeadLetter, Company.company_name, outreach_company.company_name)
        .outerjoin(Company, DeadLetter.company_id == Company.id)
        .outerjoin(Outreach, DeadLetter.outreach_id == Outreach.id)
        .outerjoin(outreach_company, Outreach.company_id == outreach_company.id)
        .order_by(DeadLetter.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return {
        "count": len(rows),
        "items": [
            {
                "id": str(dl.id),
                "company_id": str(dl.company_id) if dl.company_id else None,
                "outreach_id": str(dl.outreach_id) if dl.outreach_id else None,
                "company_name": company_name or outreach_company_name,
                "task_name": dl.task_name,
                "stage": dl.stage,
                "error_msg": dl.error_msg,
                "retry_count": dl.retry_count,
                "created_at": dl.created_at.isoformat() if dl.created_at else None,
            }
            for dl, company_name, outreach_company_name in rows
        ],
    }


@router.post("/retry")
async def retry_dead_letter(
    stage: str | None = Query(
        None, description="Only retry this stage (research/drafting/logistics)"
    ),
    session: AsyncSession = Depends(get_async_session),
    admin: User = Depends(require_admin),
):
    """Re-dispatch dead-lettered tasks: reset each row to its stage's input
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
        stage_val = dl.stage
        reset_status = _DLQ_STAGE_RESET.get(stage_val)
        if reset_status is None:
            logger.warning(f"DLQ row {dl.id} has unknown stage {stage_val!r}; skipping")
            continue

        if stage_val == "research":
            company_id = str(dl.company_id)
            company = await session.get(Company, dl.company_id)
            if company is not None:
                company.research_status = reset_status
                company.error_msg = None
            await session.delete(dl)
            research_task.delay(company_id)
        elif stage_val == "drafting":
            outreach_id = str(dl.outreach_id)
            outreach = await session.get(Outreach, dl.outreach_id)
            if outreach is not None:
                outreach.status = reset_status
                outreach.error_msg = None
            await session.delete(dl)
            drafting_task.delay()
        else:  # logistics
            outreach_id = str(dl.outreach_id)
            outreach = await session.get(Outreach, dl.outreach_id)
            if outreach is not None:
                outreach.status = reset_status
                outreach.error_msg = None
            await session.delete(dl)
            logistics_task.delay(outreach_id)

        retried += 1

    await session.commit()
    logger.info(f"Retried {retried} dead-lettered task(s)" + (f" (stage={stage})" if stage else ""))
    return {"success": True, "retried": retried}
