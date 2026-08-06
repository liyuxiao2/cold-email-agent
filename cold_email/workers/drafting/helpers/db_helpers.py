"""Database helpers for the drafting worker.

Thin, domain-specific wrappers around SQLAlchemy session ops so drafting.py
only holds Celery orchestration. Reads come from the pending_drafts view
(see migrations/002); writes go to the drafts table + leads.status.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import text

from cold_email.database import Draft, Lead, get_sync_session

logger = logging.getLogger(__name__)


@dataclass
class PendingDraft:
    """One row of the pending_drafts view: a researched lead + its latest research.

    Field names must match the view's column aliases (see migrations/002) so
    fetch_pending_drafts can build these with PendingDraft(**row).
    """

    lead_id: str
    company_name: str
    founder_name: str
    founder_email: str
    company_url: str
    raw_content: str
    tech_stack: str | None
    recent_news: str | None
    hook: str | None


def fetch_pending_drafts() -> list[PendingDraft]:
    """Return every lead ready to be drafted (status='researched'), latest research joined.

    Reading the view — not leads/research directly — is what makes the batch
    sweep idempotent: once a lead is drafted its status changes and it drops out
    of pending_drafts, so a retried sweep never double-drafts it.
    """
    with get_sync_session() as session:
        rows = session.execute(text("SELECT * FROM pending_drafts")).mappings().all()
    logger.info(f"{len(rows)} lead(s) pending drafting")
    return [PendingDraft(**row) for row in rows]


def commit_draft(
    lead_id: str,
    subject_line: str,
    body: str,
    gmail_draft_id: str,
) -> None:
    """Insert a new Draft row for the given lead."""
    with get_sync_session() as session:
        session.add(
            Draft(
                lead_id=lead_id,
                subject_line=subject_line,
                body=body,
                gmail_draft_id=gmail_draft_id,
            )
        )
        session.commit()
        logger.info(f"Draft for lead {lead_id} saved (gmail_draft_id={gmail_draft_id})")


def update_lead_status(lead_id: str, status: str, error_msg: str | None = None) -> None:
    """Update the status (and optional error message) of a lead."""
    with get_sync_session() as session:
        db_lead = session.get(Lead, lead_id)
        if db_lead:
            db_lead.status = status
            if error_msg is not None:
                db_lead.error_msg = error_msg
            session.commit()
            logger.info(f"Lead {lead_id} status updated to {status!r}")
