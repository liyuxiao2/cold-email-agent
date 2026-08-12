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


def save_founder_contact(lead_id: str, founder_name: str | None, founder_email: str) -> None:
    """Persist the founder email (and name, if research found one) on the lead.

    founder_email is what drafting requires; we also backfill founder_name when
    research resolved a better one than discovery had, for email personalization.
    """
    with get_sync_session() as session:
        lead = session.get(Lead, lead_id)
        if lead:
            lead.founder_email = founder_email
            if founder_name:
                lead.founder_name = founder_name
            session.commit()
            logger.info(f"Founder contact saved for lead {lead_id}: {founder_email}")


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
