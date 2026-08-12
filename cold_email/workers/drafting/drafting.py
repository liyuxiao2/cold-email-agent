"""Drafting worker — Celery orchestration layer.

A *batch sweep*: instead of being handed one lead_id, drafting_task queries the
pending_drafts view for every researched-but-un-drafted lead and drafts each.
Triggered on a schedule by Celery Beat (see celery_app.py), so leads that reach
'researched' between runs are collected on the next tick.

Helpers live in sibling modules:
  - generation.py  — Gemini email generation
  - db_helpers.py  — pending_drafts read, draft write, status write
"""

import logging

from celery import shared_task

from cold_email.workers.constants import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAY
from cold_email.workers.drafting.constants import ERR_EMPTY_DRAFT, ERR_NO_FOUNDER_EMAIL
from cold_email.workers.drafting.helpers.db_helpers import (
    commit_draft,
    fetch_pending_drafts,
    update_lead_status,
)
from cold_email.workers.drafting.helpers.generation import draft_email
from cold_email.workers.gmail_client import create_draft

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    name="cold_email.workers.drafting.drafting_task",
)
def drafting_task(self) -> dict:
    """Draft an email for every lead currently in pending_drafts.

    Two failure classes, handled differently per lead so one bad lead never
    aborts the whole sweep:
      * Terminal (no founder email, empty model output) → mark the lead 'failed'
        with an error_msg. It leaves the 'researched' state, so it drops out of
        pending_drafts and won't be retried on the next sweep.
      * Transient (Gemini/Gmail network hiccup) → log and leave the lead at
        'researched'. The next Beat sweep naturally retries it.

    autoretry_for only fires for errors *outside* the loop (e.g. the initial
    pending_drafts read), never for a single lead — those are caught here.
    """
    pending = fetch_pending_drafts()
    if not pending:
        return {"status": "success", "drafted": 0}

    drafted = 0
    for row in pending:
        lead_id = row.lead_id

        # Terminal: can't email a lead with no address.
        if not row.founder_email:
            update_lead_status(lead_id, "failed", error_msg=ERR_NO_FOUNDER_EMAIL)
            logger.warning(f"Lead {lead_id} has no founder_email; marked failed")
            continue

        try:
            draft = draft_email(row)

            # Terminal: a blank or malformed draft isn't worth retrying blindly.
            if not draft.get("subject") or not draft.get("body"):
                update_lead_status(lead_id, "failed", error_msg=ERR_EMPTY_DRAFT)
                logger.warning(f"Lead {lead_id} produced an empty draft; marked failed")
                continue

            gmail_draft_id = create_draft(
                to=row.founder_email,
                subject=draft["subject"],
                body=draft["body"],
            )
            commit_draft(
                lead_id=lead_id,
                subject_line=draft["subject"],
                body=draft["body"],
                gmail_draft_id=gmail_draft_id,
            )
            update_lead_status(lead_id, "drafted")
            drafted += 1

        except Exception as exc:
            # Transient: leave at 'researched' so the next sweep picks it up.
            logger.error(f"Transient failure drafting lead {lead_id}: {exc}")

    return {"status": "success", "drafted": drafted}
