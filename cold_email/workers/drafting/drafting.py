"""Drafting worker — Celery orchestration layer.

A *batch sweep*: instead of being handed one outreach_id, drafting_task queries
the pending_drafts view for every queued-but-un-drafted outreach row and drafts
each. Triggered on a schedule by Celery Beat (see celery_app.py), so outreach
rows that reach 'queued' between runs are collected on the next tick.

Helpers live in sibling modules:
  - generation.py  — LLM email generation
  - db_helpers.py  — pending_drafts read, draft write
Shared failure handling lives in cold_email.workers.shared.errors.
"""

import logging
import time
from dataclasses import dataclass

from celery import shared_task
from sqlalchemy import text

from cold_email.auth.gmail_creds import resolve_gmail_credentials
from cold_email.database import (
    OUTREACH_DRAFTED,
    OUTREACH_QUEUED,
    ROLE_ADMIN,
    Outreach,
    Profile,
    User,
    get_sync_session,
)
from cold_email.resume_store import get_resume_sync
from cold_email.sender_profile import SenderProfile
from cold_email.workers.drafting.constants import DRAFTING, ERR_EMPTY_DRAFT, ERR_NO_CONTACT_EMAIL
from cold_email.workers.drafting.helpers.db_helpers import (
    commit_draft,
    fetch_pending_drafts,
)
from cold_email.workers.drafting.helpers.generation import draft_email
from cold_email.workers.shared.constants import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAY,
    LLM_MIN_INTERVAL_SECONDS,
)
from cold_email.workers.shared.db_helpers import update_outreach_status
from cold_email.workers.shared.errors import fail_outreach, handle_transient_failure
from cold_email.workers.shared.gmail_client import GmailCredentials, create_draft

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SenderContext:
    """Everything a drafting sweep needs about the sending user."""

    profile: SenderProfile
    attachment: tuple[str, bytes] | None
    creds: GmailCredentials


def load_sender_context(session, user_id: str) -> tuple[SenderContext | None, str]:
    """Load profile, résumé, and Gmail credentials ONCE for a sweep.

    Once, not per lead: the résumé bytes cross the DB connection on every read,
    so a 40-lead sweep would pull ~16MB out of Cloud SQL to attach the same file
    40 times.

    Returns (context, reason). A None context is terminal for the SWEEP, not for
    any single row — neither missing piece can be fixed by trying another lead.
    """
    row = session.get(Profile, user_id)
    if row is None or not (row.name and row.intro):
        return None, "no_profile"

    user = session.get(User, user_id)
    creds = resolve_gmail_credentials(user)
    if creds is None:
        return None, "gmail_disconnected"

    # A missing PDF is NOT terminal: effective_resume_text falls back to
    # intro + experience_pool, so the email is still personalised.
    attachment = get_resume_sync(session, user_id)

    return SenderContext(
        profile=SenderProfile.from_row(row), attachment=attachment, creds=creds
    ), "ok"


def bridge_queue_admin_outreach() -> int:
    """TEMPORARY: queue outreach rows for the admin over every researched company.

    ================== DELETE THIS IN STACK 3 ==================
    Nothing creates outreach rows until Stack 3 adds the pool UI, so without
    this the pipeline would silently stop drafting the moment 1b lands. This
    exactly preserves today's behaviour: the admin drafts everything researched.

    Stack 3 replaces it with user selection via POST /api/outreach, and this
    function plus its call in drafting_task must be removed then.

    Selection here is simply highest-confidence eligible contact. The real
    least-globally-contacted-with-cap selection is Stack 3's.
    ============================================================
    """
    with get_sync_session() as session:
        admin = (
            session.query(User).filter(User.role == ROLE_ADMIN).order_by(User.created_at).first()
        )
        if admin is None:
            logger.warning("No admin user; bridge cannot queue outreach")
            return 0

        rows = session.execute(
            text("""
            SELECT DISTINCT ON (c.id) c.id AS company_id, ct.id AS contact_id
            FROM companies c
            JOIN company_contacts ct ON ct.company_id = c.id AND ct.eligible
            WHERE c.research_status = 'researched'
              AND NOT EXISTS (
                  SELECT 1 FROM outreach o
                  WHERE o.company_id = c.id AND o.user_id = :admin_id
              )
            ORDER BY c.id, ct.confidence DESC, ct.id
            """),
            {"admin_id": admin.id},
        ).all()

        for row in rows:
            session.add(
                Outreach(
                    user_id=admin.id,
                    company_id=row.company_id,
                    contact_id=row.contact_id,
                    status=OUTREACH_QUEUED,
                )
            )
        session.commit()
        if rows:
            logger.info(f"Bridge queued {len(rows)} outreach rows for admin")
        return len(rows)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    name="cold_email.workers.drafting.drafting_task",
)
def drafting_task(self) -> dict:
    """Draft an email for every outreach row currently in pending_drafts.

    Two preflight checks abort the WHOLE sweep before any row is touched — a
    missing profile or disconnected Gmail cannot be fixed by trying another
    lead, so rows are left at 'queued' with no dead-letter row. Completing the
    profile or reconnecting Gmail makes the next sweep pick them up with no
    manual DLQ retry.

    Once past preflight, two failure classes are handled differently per row
    so one bad row never aborts the rest of the sweep:
      * Terminal (no contact email, empty model output) → fail_outreach marks
        the row 'failed'. It leaves the 'queued' state, drops out of
        pending_drafts, and won't be retried on the next sweep.
      * Transient (LLM/Gmail network hiccup) → handle_transient_failure logs
        and leaves the row at 'queued'. The next Beat sweep retries it.

    autoretry_for only fires for errors *outside* the loop (e.g. the initial
    pending_drafts read), never for a single row — those are caught here.
    """
    # TEMPORARY (Stack 1b): see bridge_queue_admin_outreach. Remove in Stack 3.
    bridge_queue_admin_outreach()

    pending = fetch_pending_drafts()
    if not pending:
        return {"status": "success", "drafted": 0}

    # Sweeps are single-user until Stack 3 makes drafting_task(user_id).
    user_id = pending[0].user_id
    with get_sync_session() as session:
        context, reason = load_sender_context(session, user_id)
        # load_sender_context only reads. Commit rather than let the session
        # exit with a still-open transaction — an idle read transaction is a
        # connection-pool liability, and it would otherwise hold locks (e.g.
        # on pending_drafts, read a moment ago) for as long as the session
        # lives.
        session.commit()

    if context is None:
        # Leave rows at 'queued' and write NO dead-letter row: completing the
        # profile or reconnecting Gmail should make these drafts happen with no
        # manual retry.
        logger.warning(f"Sweep aborted for user {user_id}: {reason}")
        return {"status": reason, "drafted": 0}

    drafted = 0
    for row in pending:
        outreach_id = row.outreach_id

        if not row.contact_email:
            fail_outreach(
                outreach_id,
                ERR_NO_CONTACT_EMAIL,
                stage=DRAFTING,
                task_name="cold_email.workers.drafting.drafting_task",
            )
            continue

        try:
            draft = draft_email(row, context.profile)
            time.sleep(LLM_MIN_INTERVAL_SECONDS)

            if not draft.get("subject") or not draft.get("body"):
                fail_outreach(
                    outreach_id,
                    ERR_EMPTY_DRAFT,
                    stage=DRAFTING,
                    task_name="cold_email.workers.drafting.drafting_task",
                )
                continue

            gmail_draft_id = create_draft(
                context.creds,
                to=row.contact_email,
                subject=draft["subject"],
                body=draft["body"],
                html=draft.get("body_html"),
                attachment=context.attachment,
            )
            commit_draft(
                outreach_id=outreach_id,
                subject_line=draft["subject"],
                body=draft["body"],
                gmail_draft_id=gmail_draft_id,
            )
            update_outreach_status(outreach_id, OUTREACH_DRAFTED)
            drafted += 1

        except Exception as exc:
            handle_transient_failure(outreach_id, exc)

    return {"status": "success", "drafted": drafted}
