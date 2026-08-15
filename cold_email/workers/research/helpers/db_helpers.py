"""Database helpers for the research worker.

Provides thin, domain-specific wrappers around SQLAlchemy session operations
so that research.py only contains Celery orchestration logic.
"""

import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert

from cold_email.database import Company, CompanyContact, Research, get_sync_session
from cold_email.workers.research.helpers.contact_finder import ClassifiedContact

logger = logging.getLogger(__name__)


def fetch_company(company_id: str) -> Company | None:
    """Fetch a company from the database by its ID."""
    with get_sync_session() as session:
        company = session.get(Company, company_id)
        logger.info(f"Company fetched from DB: {company}")
    return company


def save_contacts(company_id: str, contacts: list[ClassifiedContact]) -> int:
    """Bulk-upsert a company's contact pool. Returns the number inserted.

    ON CONFLICT DO NOTHING against UNIQUE(company_id, email): a retried research
    task must not duplicate contacts, and re-classifying an existing row is a
    separate concern from discovering one.
    """
    if not contacts:
        return 0

    rows = [
        {
            "company_id": company_id,
            "email": c.contact.email,
            "first_name": c.contact.first_name,
            "last_name": c.contact.last_name,
            "position": c.contact.position,
            "seniority": c.contact.seniority,
            "department": c.contact.department,
            "confidence": c.contact.confidence,
            "is_founder": c.is_founder,
            "eligible": c.eligible,
        }
        for c in contacts
    ]

    with get_sync_session() as session:
        statement = (
            pg_insert(CompanyContact)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["company_id", "email"])
        )
        result = session.execute(statement)
        session.commit()
        logger.info(f"Saved {result.rowcount or 0} new contact(s) for company {company_id}")
        return result.rowcount or 0


def commit_research(
    company_id: str,
    tech_stack: list | None,
    recent_news: str | None,
    hook: str | None,
    raw_content: str | None,
) -> None:
    """Insert a new Research row for the given company."""
    with get_sync_session() as session:
        session.add(
            Research(
                company_id=company_id,
                tech_stack=tech_stack,
                recent_news=recent_news,
                hook=hook,
                raw_content=raw_content,
            )
        )
        session.commit()
        logger.info(f"Research data for company {company_id} saved to DB")
