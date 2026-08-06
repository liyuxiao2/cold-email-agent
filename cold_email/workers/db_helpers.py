"""Shared database helpers for Celery workers.

Domain-specific wrappers used across more than one worker live here; helpers
specific to a single worker stay in that worker's helpers/db_helpers.py.
"""

import logging

from cold_email.database import Lead, get_sync_session

logger = logging.getLogger(__name__)


def update_lead_status(lead_id: str, status: str, error_msg: str | None = None) -> None:
    """Update the status (and optional error message) of a lead.

    This is the single write point for the pipeline's state machine — every
    worker advances a lead through it, so it lives at the shared level.
    """
    with get_sync_session() as session:
        db_lead = session.get(Lead, lead_id)
        if db_lead:
            db_lead.status = status
            if error_msg is not None:
                db_lead.error_msg = error_msg
            session.commit()
            logger.info(f"Lead {lead_id} status updated to {status!r}")
