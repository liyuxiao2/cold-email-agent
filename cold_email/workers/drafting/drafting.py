"""Drafting worker — Celery orchestration layer.

Dispatched PER USER (`drafting_task(user_id)`) by POST /api/outreach right
after outreach rows are queued for that user. One dispatch drafts every
queued row belonging to that user — one dispatch per BATCH, not per company,
since the task already sweeps everything that user has queued.

`drafting_recovery_task` is the Beat-scheduled safety net (hourly, see
celery_app.py): it finds users with STALE queued rows — evidence a dispatch
was lost, e.g. a Redis hiccup during POST /api/outreach — and re-dispatches
drafting_task for each. It is not the primary path.

Note for readers of this stack's history: an earlier shape of this file had
drafting_task() take NO arguments and do its own per-user grouping internally
(reading every queued row across all tenants, bucketing by user_id). That was
itself a fix for a worse bug — a single-context sweep drafting EVERY pending
row with the FIRST owner's profile, résumé, and Gmail mailbox, so one user's
contact could receive mail built from a different user's identity. This
version preserves that per-user isolation by construction: drafting_task only
ever touches ONE user's rows, so cross-tenant mixing is not reachable from
here. The grouping now happens one level up, in drafting_recovery_task, for
the same reason it always existed — a single dispatch must never load two
users' credentials into one draft.

Helpers live in sibling modules:
  - generation.py  — LLM email generation
  - db_helpers.py  — pending_drafts read, draft write
Shared failure handling lives in cold_email.workers.shared.errors.
"""

import logging
from dataclasses import dataclass

from celery import shared_task
from sqlalchemy import text

from cold_email.auth.gmail_creds import resolve_gmail_credentials
from cold_email.database import (
    OUTREACH_DRAFTED,
    OUTREACH_DRAFTING,
    OUTREACH_QUEUED,
    Profile,
    User,
    get_sync_session,
)
from cold_email.resume_store import get_resume_sync
from cold_email.sender_profile import SenderProfile
from cold_email.workers.drafting.constants import DRAFTING, ERR_EMPTY_DRAFT
from cold_email.workers.drafting.helpers.db_helpers import (
    claim_pending_drafts,
    commit_draft,
    fetch_pending_drafts,
)
from cold_email.workers.drafting.helpers.generation import draft_email
from cold_email.workers.shared.constants import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAY
from cold_email.workers.shared.db_helpers import update_outreach_status
from cold_email.workers.shared.errors import fail_outreach, handle_transient_failure
from cold_email.workers.shared.gmail_client import GmailCredentials, create_draft
from cold_email.workers.shared.llm import (
    LlmAuthenticationError,
    LlmCredentials,
    resolve_llm_credentials,
)

logger = logging.getLogger(__name__)

# How stale a 'queued' row must be before the recovery sweep treats its
# dispatch as lost. Long enough that an in-flight sweep (drafting takes real
# LLM + Gmail round-trips per row) is never mistaken for a dropped one. Also
# used as the staleness bound for a claimed 'drafting' row: the same
# reasoning applies — a sweep genuinely working a row is never mistaken for
# one whose worker was killed mid-claim.
RECOVERY_STALE_MINUTES = 30

# How many times a single row may be reclaimed from a stale 'drafting' claim
# back to 'queued' before the recovery sweep gives up on it. Reclaiming is
# safe (the claim's compare-and-swap prevents a concurrent duplicate, and no
# Gmail draft exists until after the LLM call succeeds) but not infinitely so:
# a row that keeps landing back at 'drafting' and going stale again is a row
# that keeps crashing its worker, and requeuing it forever would recreate the
# exact silent-infinite-retry problem the DLQ exists to prevent.
MAX_DRAFTING_RECLAIMS = 3


@dataclass(frozen=True)
class SenderContext:
    """Everything a drafting sweep needs about the sending user."""

    profile: SenderProfile
    attachment: tuple[str, bytes] | None
    creds: GmailCredentials
    llm_credentials: LlmCredentials


def load_sender_context(session, user_id: str) -> tuple[SenderContext | None, str]:
    """Load profile, résumé, Gmail credentials, and LLM credentials ONCE for a
    sweep.

    Once, not per row: the résumé bytes cross the DB connection on every read,
    so a 40-row sweep would pull ~16MB out of Cloud SQL to attach the same
    file 40 times.

    Returns (context, reason). A None context is terminal for the SWEEP, not
    for any single row — neither missing piece can be fixed by trying another
    row.
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
    llm_credentials = resolve_llm_credentials(user)

    return SenderContext(
        profile=SenderProfile.from_row(row),
        attachment=attachment,
        creds=creds,
        llm_credentials=llm_credentials,
    ), "ok"


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=DEFAULT_MAX_RETRIES,
    default_retry_delay=DEFAULT_RETRY_DELAY,
    name="cold_email.workers.drafting.drafting_task",
)
def drafting_task(self, user_id: str) -> dict:
    """Draft an email for every queued outreach row belonging to ONE user.

    Dispatched by POST /api/outreach right after that user's rows are queued.
    The Beat schedule no longer calls this directly — it runs
    drafting_recovery_task instead, which re-dispatches this task per user
    for rows whose original dispatch appears to have been lost.

    A missing profile or disconnected Gmail aborts the whole SWEEP (this
    user's batch), not any single row — neither problem can be fixed by
    trying another row. It leaves this user's rows at 'queued' with no
    dead-letter row, so completing the profile or reconnecting Gmail makes
    the next dispatch (a manual regenerate, or the recovery sweep) pick them
    up with no manual DLQ retry.

    Once past preflight, three failure classes are handled differently per
    row so one bad row never aborts the rest of the batch:
      * Terminal, empty output → fail_outreach marks the row 'failed'. It
        drops out of pending_drafts and is not retried automatically.
        (contact_email is never missing here: pending_drafts INNER JOINs
        company_contacts on a NOT NULL email.)
      * Terminal, LLM auth failure (LlmAuthenticationError, e.g. a rejected
        BYOK key) → also fail_outreach: a wrong key will never succeed on
        retry, so treating it as transient would retry it every recovery
        sweep forever with nothing the user could see or act on.
      * Transient (LLM/Gmail network hiccup) → handle_transient_failure logs
        and records the reason, and the row is explicitly requeued to
        'queued' (releasing this sweep's claim — see claim_pending_drafts)
        so the recovery sweep retries it later.

    autoretry_for only fires for errors *outside* the per-row loop (e.g. the
    initial pending_drafts read), never for a single row — those are caught
    here.
    """
    pending = fetch_pending_drafts(user_id)
    if not pending:
        return {"status": "success", "drafted": 0}

    with get_sync_session() as session:
        context, reason = load_sender_context(session, user_id)
        # load_sender_context only reads. Commit rather than let the session
        # exit with a still-open transaction — an idle read transaction is a
        # connection-pool liability, and it would otherwise hold locks (e.g.
        # on pending_drafts, read a moment ago) for as long as the session
        # lives.
        session.commit()

    if context is None:
        # Leave this user's rows at 'queued' and write NO dead-letter row:
        # completing the profile or reconnecting Gmail should make these
        # drafts happen with no manual retry. Claiming happens AFTER this
        # check specifically so an aborted sweep never needs to release a
        # claim it never took.
        logger.warning(f"Sweep aborted for user {user_id}: {reason}")
        return {"status": reason, "drafted": 0}

    # Claim before working: two dispatches racing over the same queued rows
    # (a double click, a Regenerate landing mid-sweep, the hourly recovery
    # sweep) would otherwise both read the same rows from pending_drafts and
    # both draft them — two `drafts` rows and two Gmail drafts to the same
    # contact, invisible to the user because the review deck only shows the
    # newest. The claim's own WHERE status = 'queued' is the compare-and-swap:
    # whichever dispatch's UPDATE commits first wins each row; the other sees
    # it already claimed and silently skips it.
    # str(): fetch_pending_drafts reads outreach_id back as a real uuid.UUID
    # (psycopg2's default UUID adapter), despite PendingDraft's `str` type
    # hint — normalize both sides so the membership check below isn't
    # comparing a UUID to its own string representation.
    claimed_ids = claim_pending_drafts([str(row.outreach_id) for row in pending])
    pending = [row for row in pending if str(row.outreach_id) in claimed_ids]
    if not pending:
        return {"status": "success", "drafted": 0}

    drafted = 0
    for row in pending:
        outreach_id = row.outreach_id

        try:
            # No time.sleep: the token bucket inside generate_json paces the
            # whole fleet, which is the actual shared constraint.
            draft = draft_email(row, context.profile, credentials=context.llm_credentials)

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

        except LlmAuthenticationError as exc:
            # Terminal, not transient: a rejected BYOK key will never succeed
            # on retry, so looping it through handle_transient_failure would
            # retry it every recovery sweep forever with nothing the user
            # could act on. fail_outreach both marks the row 'failed' (out of
            # 'drafting', so it's never mistaken for a lost claim) and writes
            # a DLQ row the user can see and retry once the key is fixed.
            fail_outreach(
                outreach_id,
                f"LLM authentication failed: {exc}",
                stage=DRAFTING,
                task_name="cold_email.workers.drafting.drafting_task",
            )

        except Exception as exc:
            handle_transient_failure(outreach_id, exc)
            # Release this sweep's claim: handle_transient_failure only
            # records the reason and never touches status, so without this
            # the row would stay stuck at 'drafting' — invisible to the next
            # sweep's pending_drafts read (a Python exception is handled
            # immediately, right here; only a hard process crash that skips
            # this except block entirely needs drafting_recovery_task's
            # separate stale-'drafting' reclaim below).
            update_outreach_status(outreach_id, OUTREACH_QUEUED)

    return {"status": "success", "drafted": drafted}


_RECLAIM_STALE_DRAFTING_SQL = text("""
    UPDATE outreach
    SET status = :queued, reclaim_count = reclaim_count + 1, error_msg = :error_msg
    WHERE status = :drafting
      AND updated_at < now() - make_interval(mins => :mins)
      AND reclaim_count < :max_reclaims
    RETURNING id, user_id
""")

_STALE_DRAFTING_OVER_CAP_SQL = text("""
    SELECT id FROM outreach
    WHERE status = :drafting
      AND updated_at < now() - make_interval(mins => :mins)
      AND reclaim_count >= :max_reclaims
""")

_RECLAIMED_ERROR_MSG = (
    f"Drafting claim stale for over {RECOVERY_STALE_MINUTES} minutes (worker likely "
    "crashed mid-draft); returned to queued for retry."
)

_RECLAIM_CAP_EXCEEDED_MSG = (
    f"Drafting claim reclaimed {MAX_DRAFTING_RECLAIMS} time(s) without completing "
    "(worker keeps crashing on this row); giving up and dead-lettering."
)


@shared_task(name="cold_email.workers.drafting.drafting_recovery_task")
def drafting_recovery_task() -> dict:
    """Re-dispatch drafting for users with stale queued rows, and reclaim rows
    stuck at 'drafting'.

    A safety net, not the primary path. Two distinct things can strand a row,
    and this sweep recovers from both:

      * A Redis hiccup during POST /api/outreach leaves rows 'queued' forever
        with no explanation the user can see or act on — the original reason
        this task exists.
      * A hard Celery process crash (SIGKILL, OOM, container eviction) between
        claim_pending_drafts moving a row to 'drafting' and the row finishing
        leaves it claimed forever — not a Python exception, so drafting_task's
        own per-row `except Exception` never runs to release it. Below
        MAX_DRAFTING_RECLAIMS, the row is returned to 'queued' so the normal
        path retries it (safe: the claim's compare-and-swap still prevents a
        second concurrent draft, and no Gmail draft exists yet). Past the cap,
        it's dead-lettered via fail_outreach instead of requeued forever —
        the same failure choke point as any other terminal drafting error, so
        a row that keeps crashing its worker becomes visible in the DLQ
        rather than looping silently.

    Reads `outreach` directly (not the pending_drafts view, which has no
    notion of "how long queued"/"how long claimed") so the staleness cutoff
    can be expressed directly in the query. The 'drafting' cutoff relies on
    claim_pending_drafts setting updated_at = now() explicitly at claim time —
    a raw text() UPDATE bypasses the ORM, so Outreach.updated_at's
    onupdate=func.now() would otherwise never fire and every claimed row
    would look infinitely stale (or never stale, depending on when it was
    created).
    """
    with get_sync_session() as session:
        user_ids = {
            str(user_id)
            for user_id in session.execute(
                text("""
                    SELECT DISTINCT user_id FROM outreach
                    WHERE status = 'queued'
                      AND created_at < now() - make_interval(mins => :mins)
                """),
                {"mins": RECOVERY_STALE_MINUTES},
            )
            .scalars()
            .all()
        }

        reclaimed = (
            session.execute(
                _RECLAIM_STALE_DRAFTING_SQL,
                {
                    "queued": OUTREACH_QUEUED,
                    "drafting": OUTREACH_DRAFTING,
                    "mins": RECOVERY_STALE_MINUTES,
                    "max_reclaims": MAX_DRAFTING_RECLAIMS,
                    "error_msg": _RECLAIMED_ERROR_MSG,
                },
            )
            .mappings()
            .all()
        )
        # Fold reclaimed rows' owners into the same dispatch set: they're
        # already 'queued' as of this transaction, so there's no reason to
        # make them wait for next hour's stale-queued query to notice.
        user_ids.update(str(row["user_id"]) for row in reclaimed)

        over_cap_ids = (
            session.execute(
                _STALE_DRAFTING_OVER_CAP_SQL,
                {
                    "drafting": OUTREACH_DRAFTING,
                    "mins": RECOVERY_STALE_MINUTES,
                    "max_reclaims": MAX_DRAFTING_RECLAIMS,
                },
            )
            .scalars()
            .all()
        )
        session.commit()

    # Outside the session above, same choke point every other terminal
    # drafting failure uses: dead-letters the row AND marks it 'failed', so it
    # stops matching the query above on the next run (self-cleaning) and shows
    # up in the DLQ for a human to inspect.
    for outreach_id in over_cap_ids:
        fail_outreach(
            str(outreach_id),
            _RECLAIM_CAP_EXCEEDED_MSG,
            stage=DRAFTING,
            task_name="cold_email.workers.drafting.drafting_recovery_task",
        )

    # Wrapped per-user: this is the sweep that exists specifically to recover
    # from a broker hiccup, so a Redis blip on THIS dispatch must not itself
    # abort the loop and skip every remaining user for the hour.
    swept = 0
    for user_id in user_ids:
        try:
            drafting_task.delay(str(user_id))
            swept += 1
        except Exception as exc:
            logger.warning(f"Could not re-dispatch drafting_task for user {user_id}: {exc}")

    if swept:
        logger.info(f"Recovery sweep re-dispatched drafting for {swept} user(s)")
    if reclaimed:
        logger.warning(f"Recovery sweep reclaimed {len(reclaimed)} stale 'drafting' row(s)")
    if over_cap_ids:
        logger.warning(
            f"Recovery sweep dead-lettered {len(over_cap_ids)} row(s) stuck at "
            "'drafting' past the reclaim cap"
        )
    return {
        "status": "success",
        "users_swept": swept,
        "reclaimed": len(reclaimed),
        "dead_lettered": len(over_cap_ids),
    }
