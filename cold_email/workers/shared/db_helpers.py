"""Shared database helpers for Celery workers.

Domain-specific wrappers used across more than one worker live here; helpers
specific to a single worker stay in that worker's helpers/db_helpers.py.
"""

import logging

from cold_email.database import Company, DeadLetter, Outreach, get_sync_session

logger = logging.getLogger(__name__)


def update_company_research_status(
    company_id: str, status: str, error_msg: str | None = None
) -> None:
    """Set a company's GLOBAL research status (found | researched | failed)."""
    with get_sync_session() as session:
        company = session.get(Company, company_id)
        if company is None:
            logger.warning(f"Company {company_id} not found; cannot set status {status}")
            return
        company.research_status = status
        if error_msg is not None:
            company.error_msg = error_msg
        session.commit()


def update_outreach_status(outreach_id: str, status: str, error_msg: str | None = None) -> None:
    """Set one user's PER-USER outreach status."""
    with get_sync_session() as session:
        outreach = session.get(Outreach, outreach_id)
        if outreach is None:
            logger.warning(f"Outreach {outreach_id} not found; cannot set status {status}")
            return
        outreach.status = status
        if error_msg is not None:
            outreach.error_msg = error_msg
        session.commit()


def record_dead_letter(
    *,
    task_name: str,
    stage: str,
    error_msg: str,
    company_id: str | None = None,
    outreach_id: str | None = None,
) -> None:
    """Write a DLQ row at exactly one level.

    Keyword-only and exclusive by construction: the CHECK constraint on the
    table is the backstop, but passing neither (or both) is a programming
    error worth catching here, where the traceback names the caller.
    """
    if not (bool(company_id) ^ bool(outreach_id)):
        raise ValueError("record_dead_letter requires exactly one of company_id / outreach_id")

    with get_sync_session() as session:
        session.add(
            DeadLetter(
                company_id=company_id,
                outreach_id=outreach_id,
                task_name=task_name,
                stage=stage,
                error_msg=error_msg,
            )
        )
        session.commit()
