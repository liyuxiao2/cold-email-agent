"""Database helpers for the research worker.

Provides thin, domain-specific wrappers around SQLAlchemy session operations
so that research.py only contains Celery orchestration logic.
"""

import logging

from cold_email.database import Lead, Research, SyncSessionLocal, get_sync_session

logger = logging.getLogger(__name__)


def fetch_lead(lead_id: str) -> Lead | None:
    """Fetch a lead from the database by its ID."""
    with SyncSessionLocal() as session:
        lead = session.get(Lead, lead_id)
        logger.info(f"Lead fetched from DB: {lead}")
    return lead


def commit_research(
    lead_id: str,
    tech_stack: list | None,
    recent_news: str | None,
    hook: str | None,
    raw_content: str | None,
) -> None:
    """Insert a new Research row for the given lead."""
    with get_sync_session() as session:
        session.add(
            Research(
                lead_id=lead_id,
                tech_stack=tech_stack,
                recent_news=recent_news,
                hook=hook,
                raw_content=raw_content,
            )
        )
        session.commit()
        logger.info(f"Research data for lead {lead_id} saved to DB")


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
