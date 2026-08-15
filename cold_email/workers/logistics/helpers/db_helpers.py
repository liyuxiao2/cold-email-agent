"""Database helpers for the logistics worker.

Thin, domain-specific wrappers so logistics.py only holds Celery
orchestration -- same split as drafting's helpers/db_helpers.py. All raw SQL
lives here (not in logistics.py) because tests/conftest.py's sync_session_for
fixture only monkeypatches get_sync_session in *this* module, not in
logistics.py itself.
"""

import logging

from sqlalchemy import text

from cold_email.database import Outreach, User, get_sync_session
from cold_email.workers.shared.views import PendingSend

logger = logging.getLogger(__name__)

# The claim. Selecting and marking in ONE statement is what makes the scanner
# safe: two overlapping runs cannot both claim a row, because the second
# UPDATE's own WHERE status = 'approved' matches nothing once the first has
# already flipped it to 'sending'. Same idiom as drafting's _CLAIM_SQL (see
# cold_email/workers/drafting/helpers/db_helpers.py) -- one claim mechanism
# for the codebase, not two. Sets updated_at = now() explicitly: this is a
# raw text() UPDATE, which bypasses the ORM unit-of-work entirely, so
# Outreach.updated_at's onupdate=func.now() never fires on its own --
# reap_stuck_sends needs that timestamp to measure how long a row has been
# claimed.
_CLAIM_DUE_SQL = text("""
    UPDATE outreach
    SET status = 'sending', updated_at = now()
    WHERE id IN (SELECT outreach_id FROM pending_sends)
      AND status = 'approved'
    RETURNING id
""")

_STUCK_SENDING_SQL = text("""
    SELECT id FROM outreach
    WHERE status = 'sending'
      AND updated_at < now() - make_interval(mins => :mins)
""")

# The second claim. claim_due_sends (above) is the scanner's claim -- it
# prevents two overlapping SCANS from both dispatching the same row. This one
# prevents two concurrent EXECUTIONS of the SAME already-dispatched task
# (Celery's at-least-once delivery can redeliver one message to two workers)
# from both reaching send_draft: a bare status re-check is read-then-act, not
# atomic, since both would read 'sending' before either advances it. Nulling
# drafts.gmail_draft_id -- the one column that names the resource about to be
# spent -- is a compare-and-swap in the same idiom: whichever caller's UPDATE
# still finds the expected (non-null) value wins and consumes it; the other's
# WHERE clause matches nothing once the winner has already claimed it.
_CLAIM_SEND_TICKET_SQL = text("""
    UPDATE drafts
    SET gmail_draft_id = NULL
    WHERE id = :draft_id AND gmail_draft_id = :gmail_draft_id
    RETURNING id
""")


def claim_due_sends() -> list[str]:
    """Atomically flip every due 'approved' row to 'sending' and return the
    ids ACTUALLY claimed by this call.

    pending_sends already carries the `scheduled_send_at IS NULL OR <= now()`
    clause (migration 006), so this claims both immediate approvals and
    schedules whose time has come. Callers must dispatch logistics_task only
    for the ids this function returns -- a row another concurrent scan already
    claimed is silently absent from the result.
    """
    with get_sync_session() as session:
        claimed = session.execute(_CLAIM_DUE_SQL).scalars().all()
        session.commit()
    return [str(outreach_id) for outreach_id in claimed]


def find_stuck_sending(minutes: int) -> list[str]:
    """Ids claimed 'sending' for longer than `minutes` -- a worker crashed
    mid-send and never reached 'sent' or 'failed'."""
    with get_sync_session() as session:
        stuck = session.execute(_STUCK_SENDING_SQL, {"mins": minutes}).scalars().all()
    return [str(outreach_id) for outreach_id in stuck]


def claim_send_ticket(draft_id: str, gmail_draft_id: str) -> bool:
    """Atomically consume the one-time ticket to call send_draft for one row.

    Returns True if THIS call won the claim (drafts.gmail_draft_id was still
    `gmail_draft_id` and is now NULL), False if it was already spent -- by a
    truly concurrent execution racing this one, or by an earlier execution of
    this same row (a prior attempt that got this far before failing further
    down). Either way, False means send_draft must not be called again here.
    """
    with get_sync_session() as session:
        claimed = session.execute(
            _CLAIM_SEND_TICKET_SQL, {"draft_id": draft_id, "gmail_draft_id": gmail_draft_id}
        ).first()
        session.commit()
    return claimed is not None


def fetch_outreach_status_and_owner(outreach_id: str) -> tuple[str, str] | None:
    """Return (status, user_id) for one outreach row, or None if it doesn't exist.

    Read directly from `outreach`, not pending_sends: once a row is claimed
    'sending' it no longer matches pending_sends' `WHERE status = 'approved'`,
    but logistics_task's second guard needs to see the row's CURRENT status
    (including 'sending') to catch a duplicate Celery delivery.
    """
    with get_sync_session() as session:
        outreach = session.get(Outreach, outreach_id)
        if outreach is None:
            return None
        return outreach.status, str(outreach.user_id)


def fetch_send_row(outreach_id: str) -> PendingSend | None:
    """The sendable content for one outreach row -- contact email plus the
    latest draft's subject/body/gmail_draft_id -- keyed by id, regardless of
    the row's own status.

    Deliberately does NOT read pending_sends: by the time logistics_task
    calls this, the row has already been claimed 'sending' and no longer
    matches that view's `WHERE status = 'approved'`.
    """
    with get_sync_session() as session:
        row = (
            session.execute(
                text("""
                    SELECT o.id AS outreach_id, o.user_id, ct.email AS contact_email,
                           d.id AS draft_id, d.gmail_draft_id, d.subject_line, d.body
                    FROM outreach o
                    JOIN company_contacts ct ON ct.id = o.contact_id
                    JOIN drafts d ON d.outreach_id = o.id
                    WHERE o.id = :outreach_id
                    ORDER BY d.created_at DESC
                    LIMIT 1
                """),
                {"outreach_id": outreach_id},
            )
            .mappings()
            .first()
        )
    return PendingSend(**row) if row else None


def fetch_owning_user(user_id: str) -> User | None:
    """Return the User row that owns an outreach row, or None if it no longer exists.

    Sending must always use the OWNING user's mailbox, never a global or the
    caller's -- this is the only place logistics_task looks up a user.
    """
    with get_sync_session() as session:
        return session.get(User, user_id)
