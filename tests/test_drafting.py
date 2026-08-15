from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

from cold_email.database import OUTREACH_DRAFTED, OUTREACH_QUEUED
from cold_email.workers.drafting.drafting import drafting_task
from cold_email.workers.shared.gmail_client import GmailCredentials
from cold_email.workers.shared.llm import LlmCredentials
from cold_email.workers.shared.views import PendingDraft
from tests.conftest import _add_queued_outreach

OUTREACH_A = "00000000-0000-0000-0000-00000000000a"
OUTREACH_B = "00000000-0000-0000-0000-00000000000b"
USER_1 = "00000000-0000-0000-0000-0000000000u1"

# A fake SenderContext for tests that only exercise the per-row loop, not
# load_sender_context itself — profile/creds are never dereferenced because
# draft_email and create_draft are patched out in these tests too.
_FAKE_CONTEXT = SimpleNamespace(
    profile=object(),
    attachment=None,
    creds=GmailCredentials(
        refresh_token="rt-fake",  # noqa: S106 (test fixture, not a real credential)
        sender_email="sender@example.com",
    ),
    llm_credentials=LlmCredentials(api_key=None, provider=None, is_byok=False),
)


def _pending_row(outreach_id, contact_email="contact@acme.com", user_id=USER_1):
    return PendingDraft(
        outreach_id=outreach_id,
        user_id=user_id,
        company_id="00000000-0000-0000-0000-0000000000c1",
        contact_id="00000000-0000-0000-0000-0000000000ct1",
        company_name="Acme",
        company_url="https://acme.com",
        founder_name="Ada",
        contact_email=contact_email,
        contact_first_name="Ada",
        contact_last_name="Lovelace",
        contact_position="Founder",
        raw_content="Mock raw content",
        tech_stack="Python",
        recent_news="Raised a seed round",
        hook="Ledger scaling pain",
    )


def test_the_bridge_is_gone():
    """Stack 1b's bridge named this stack as its removal point. Leaving it in
    would silently draft every researched company for the admin forever."""
    import cold_email.workers.drafting.drafting as drafting

    assert not hasattr(drafting, "bridge_queue_admin_outreach")


def test_drafting_sweep_empty():
    """No pending outreach rows -> the sweep is a no-op returning drafted: 0."""
    with patch("cold_email.workers.drafting.drafting.fetch_pending_drafts", return_value=[]):
        result = drafting_task(USER_1)
    assert result == {"status": "success", "drafted": 0}


def test_drafting_sweep_happy_path():
    """A draftable outreach row is generated, drafted in Gmail, persisted, and advanced."""
    with (
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(OUTREACH_A)],
        ),
        patch(
            "cold_email.workers.drafting.drafting.load_sender_context",
            return_value=(_FAKE_CONTEXT, "ok"),
        ),
        patch(
            "cold_email.workers.drafting.drafting.draft_email",
            return_value={
                "subject": "Hi",
                "body": "A specific, short note.",
                "body_html": "<p>A specific, short note.</p>",
            },
        ),
        patch(
            "cold_email.workers.drafting.drafting.create_draft", return_value="gmail-123"
        ) as mock_create,
        patch("cold_email.workers.drafting.drafting.commit_draft") as mock_commit,
        patch("cold_email.workers.drafting.drafting.update_outreach_status") as mock_status,
        patch(
            "cold_email.workers.drafting.drafting.claim_pending_drafts",
            side_effect=lambda ids: set(ids),
        ),
    ):
        result = drafting_task(USER_1)

    assert result == {"status": "success", "drafted": 1}
    args, kwargs = mock_create.call_args
    assert args[0] is _FAKE_CONTEXT.creds
    assert kwargs["to"] == "contact@acme.com"
    assert kwargs["subject"] == "Hi"
    assert kwargs["body"] == "A specific, short note."
    assert kwargs["html"] == "<p>A specific, short note.</p>"
    # The per-user résumé (SenderContext.attachment) is threaded through as-is;
    # the fake context here has none.
    assert kwargs["attachment"] is None
    mock_commit.assert_called_once()
    mock_status.assert_called_once_with(OUTREACH_A, OUTREACH_DRAFTED)


def test_drafting_marks_empty_draft_failed():
    """An empty/malformed model draft is terminal for that outreach row."""
    with (
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(OUTREACH_A)],
        ),
        patch(
            "cold_email.workers.drafting.drafting.load_sender_context",
            return_value=(_FAKE_CONTEXT, "ok"),
        ),
        patch("cold_email.workers.drafting.drafting.draft_email", return_value={}),
        patch("cold_email.workers.drafting.drafting.create_draft") as mock_create,
        patch("cold_email.workers.drafting.drafting.fail_outreach") as mock_fail,
        patch(
            "cold_email.workers.drafting.drafting.claim_pending_drafts",
            side_effect=lambda ids: set(ids),
        ),
    ):
        result = drafting_task(USER_1)

    assert result == {"status": "success", "drafted": 0}
    mock_create.assert_not_called()
    assert mock_fail.call_args.args[0] == OUTREACH_A


def test_llm_auth_failure_is_terminal_not_transient():
    """A rejected BYOK key must fail_outreach (terminal, DLQ'd, visible to the
    user), never handle_transient_failure — retrying the same key on the next
    recovery sweep can't ever succeed, so looping it would retry forever with
    nothing the user could act on."""
    from cold_email.workers.shared.llm import LlmAuthenticationError

    with (
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(OUTREACH_A)],
        ),
        patch(
            "cold_email.workers.drafting.drafting.load_sender_context",
            return_value=(_FAKE_CONTEXT, "ok"),
        ),
        patch(
            "cold_email.workers.drafting.drafting.draft_email",
            side_effect=LlmAuthenticationError("bad key"),
        ),
        patch(
            "cold_email.workers.drafting.drafting.claim_pending_drafts",
            side_effect=lambda ids: set(ids),
        ),
        patch("cold_email.workers.drafting.drafting.fail_outreach") as mock_fail,
        patch("cold_email.workers.drafting.drafting.handle_transient_failure") as mock_transient,
    ):
        result = drafting_task(USER_1)

    assert result == {"status": "success", "drafted": 0}
    mock_transient.assert_not_called()
    mock_fail.assert_called_once()
    assert mock_fail.call_args.args[0] == OUTREACH_A
    assert "bad key" in mock_fail.call_args.args[1]


def test_drafting_one_bad_outreach_does_not_abort_sweep():
    """A transient failure on one row leaves it for the next sweep; others still draft."""
    with (
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(OUTREACH_A), _pending_row(OUTREACH_B)],
        ),
        patch(
            "cold_email.workers.drafting.drafting.load_sender_context",
            return_value=(_FAKE_CONTEXT, "ok"),
        ),
        patch(
            "cold_email.workers.drafting.drafting.draft_email",
            return_value={"subject": "Hi", "body": "Body"},
        ),
        # First create_draft raises (transient), second succeeds.
        patch(
            "cold_email.workers.drafting.drafting.create_draft",
            side_effect=[RuntimeError("gmail down"), "gmail-456"],
        ),
        patch("cold_email.workers.drafting.drafting.commit_draft"),
        patch("cold_email.workers.drafting.drafting.update_outreach_status") as mock_status,
        patch("cold_email.workers.drafting.drafting.handle_transient_failure") as mock_transient,
        patch(
            "cold_email.workers.drafting.drafting.claim_pending_drafts",
            side_effect=lambda ids: set(ids),
        ),
    ):
        result = drafting_task(USER_1)

    # Only the second row drafted; the first's claim is released back to
    # 'queued' so the next sweep retries it.
    assert result == {"status": "success", "drafted": 1}
    assert mock_status.call_args_list == [
        call(OUTREACH_A, OUTREACH_QUEUED),
        call(OUTREACH_B, OUTREACH_DRAFTED),
    ]
    mock_transient.assert_called_once()
    assert mock_transient.call_args.args[0] == OUTREACH_A


@pytest.mark.asyncio
async def test_empty_model_output_fails_only_that_outreach_row(
    async_session,
    admin_user_id,
    monkeypatch,
    sync_session_for,
    pending_views,
    admin_profile,
    admin_gmail_connected,
):
    """One bad row must not abort the sweep."""
    from cold_email.database import (
        OUTREACH_DRAFTED,
        OUTREACH_FAILED,
        RESEARCH_RESEARCHED,
        Company,
        CompanyContact,
        DeadLetter,
        Outreach,
        Research,
    )

    good_company = Company(company_name="Acme", research_status=RESEARCH_RESEARCHED)
    bad_company = Company(company_name="Globex", research_status=RESEARCH_RESEARCHED)
    async_session.add_all([good_company, bad_company])
    await async_session.commit()

    good_contact = CompanyContact(
        company_id=good_company.id,
        email="good@acme.com",
        first_name="Ann",
        eligible=True,
        confidence=90,
    )
    bad_contact = CompanyContact(
        company_id=bad_company.id,
        email="bad@globex.com",
        first_name="Bea",
        eligible=True,
        confidence=90,
    )
    async_session.add_all(
        [
            good_contact,
            bad_contact,
            Research(
                company_id=good_company.id,
                tech_stack=["python"],
                recent_news="news",
                hook="hook",
                raw_content="raw",
            ),
            Research(
                company_id=bad_company.id,
                tech_stack=["python"],
                recent_news="news",
                hook="hook",
                raw_content="raw",
            ),
        ]
    )
    await async_session.commit()

    # A queued outreach row per company — nothing creates these implicitly
    # anymore now that the Stack 1b bridge is gone.
    async_session.add_all(
        [
            Outreach(user_id=admin_user_id, company_id=good_company.id, contact_id=good_contact.id),
            Outreach(user_id=admin_user_id, company_id=bad_company.id, contact_id=bad_contact.id),
        ]
    )
    await async_session.commit()

    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.draft_email",
        lambda row, profile, credentials=None: (
            {} if row.contact_email == "bad@globex.com" else {"subject": "Hi", "body": "Body"}
        ),
    )
    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.create_draft", lambda creds, **kwargs: "gmail-1"
    )

    from cold_email.workers.drafting.drafting import drafting_task

    result = drafting_task(str(admin_user_id))

    assert result["drafted"] == 1

    from sqlalchemy import select

    outreach_rows = (await async_session.execute(select(Outreach))).scalars().all()
    assert {o.status for o in outreach_rows} == {OUTREACH_DRAFTED, OUTREACH_FAILED}

    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert dl.outreach_id is not None
    assert dl.company_id is None
    assert dl.stage == "drafting"


@pytest.mark.asyncio
async def test_missing_profile_leaves_the_sweep_and_rows_queued(
    async_session, admin_user_id, sync_session_for, queued_outreach
):
    """Rows stay queued rather than failing: finishing the profile should make
    these drafts happen, with no DLQ retry needed."""
    from cold_email.database import OUTREACH_QUEUED, DeadLetter
    from cold_email.workers.drafting.drafting import drafting_task

    result = drafting_task(str(admin_user_id))
    assert result == {"status": "no_profile", "drafted": 0}

    await async_session.refresh(queued_outreach)
    assert queued_outreach.status == OUTREACH_QUEUED

    from sqlalchemy import func, select

    assert (
        await async_session.execute(select(func.count()).select_from(DeadLetter))
    ).scalar_one() == 0


@pytest.mark.asyncio
async def test_missing_gmail_credentials_aborts_the_sweep(
    async_session, admin_user_id, sync_session_for, queued_outreach, admin_profile
):
    from cold_email.database import OUTREACH_QUEUED
    from cold_email.workers.drafting.drafting import drafting_task

    result = drafting_task(str(admin_user_id))
    assert result == {"status": "gmail_disconnected", "drafted": 0}

    await async_session.refresh(queued_outreach)
    assert queued_outreach.status == OUTREACH_QUEUED


@pytest.mark.asyncio
async def test_profile_without_a_pdf_drafts_with_no_attachment(
    async_session,
    admin_user_id,
    sync_session_for,
    queued_outreach,
    admin_profile_no_pdf,
    admin_gmail_connected,
    monkeypatch,
    captured_drafts,
):
    from cold_email.workers.drafting.drafting import drafting_task

    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.draft_email",
        lambda row, profile, credentials=None: {
            "subject": "Hi",
            "body": "Body",
            "body_html": "<p>Body</p>",
        },
    )

    drafting_task(str(admin_user_id))
    assert captured_drafts[0]["attachment"] is None


@pytest.mark.asyncio
async def test_resume_is_read_once_per_sweep_not_per_lead(
    async_session,
    admin_user_id,
    sync_session_for,
    three_queued_outreach,
    admin_profile,
    admin_gmail_connected,
    monkeypatch,
):
    """The bytes cross the DB connection on every read. A 40-lead sweep reading
    per lead would pull ~16MB out of Cloud SQL to attach the same file."""
    reads = []
    import cold_email.workers.drafting.drafting as drafting_module

    original = drafting_module.get_resume_sync

    def counting(session, user_id):
        reads.append(user_id)
        return original(session, user_id)

    monkeypatch.setattr(drafting_module, "get_resume_sync", counting)
    monkeypatch.setattr(
        drafting_module,
        "draft_email",
        lambda row, profile, credentials=None: {
            "subject": "Hi",
            "body": "Body",
            "body_html": "<p>Body</p>",
        },
    )
    monkeypatch.setattr(drafting_module, "create_draft", lambda creds, **kwargs: "gmail-fake")

    drafting_module.drafting_task(str(admin_user_id))
    assert len(reads) == 1, f"résumé read {len(reads)} times for 3 leads"


@pytest.mark.asyncio
async def test_sweeps_only_the_given_users_rows(
    async_session,
    two_users_queued,
    sync_session_for,
    profiles_for_both,
    captured_drafts,
    monkeypatch,
):
    """Tenancy isolation in the worker: user A's sweep must not draft user B's
    rows, which would send B's outreach from A's mailbox."""
    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.draft_email",
        lambda row, profile, credentials=None: {
            "subject": "Hi",
            "body": "Body",
            "body_html": "<p>Body</p>",
        },
    )

    from cold_email.workers.drafting.drafting import drafting_task

    result = drafting_task(str(two_users_queued["user_a"]))
    assert result["drafted"] == 1

    recipients = [d["to"] for d in captured_drafts]
    assert recipients == ["a-contact@acme.com"]


@pytest.mark.asyncio
async def test_recovery_sweep_only_picks_up_stale_rows(
    async_session, sync_session_for, stale_and_fresh_queued, profiles_for_both, captured_drafts
):
    """A safety net for a dropped dispatch, not the primary path. Without it a
    Redis hiccup during POST /api/outreach leaves rows queued forever with no
    user-visible explanation."""
    from cold_email.workers.drafting.drafting import drafting_recovery_task

    result = drafting_recovery_task()
    assert result["users_swept"] == 1


@pytest.mark.asyncio
async def test_recovery_sweep_survives_one_users_dispatch_failure(
    async_session, sync_session_for, monkeypatch
):
    """A Redis hiccup on ONE user's re-dispatch — the exact scenario this
    sweep exists to recover from — must not abort the loop and skip every
    remaining stale user for the hour."""
    from datetime import UTC, datetime, timedelta

    from cold_email.database import ROLE_USER, User

    user_a = User(email="stale-a@example.com", google_sub="sub-stale-a", role=ROLE_USER)
    user_b = User(email="stale-b@example.com", google_sub="sub-stale-b", role=ROLE_USER)
    async_session.add_all([user_a, user_b])
    await async_session.commit()

    outreach_a = await _add_queued_outreach(async_session, user_a.id, "AcmeCo", "a@acme.com")
    outreach_b = await _add_queued_outreach(async_session, user_b.id, "GlobexCo", "b@globex.com")
    stale_at = datetime.now(UTC) - timedelta(minutes=45)
    outreach_a.created_at = stale_at
    outreach_b.created_at = stale_at
    await async_session.commit()

    dispatched = []

    def fake_delay(user_id):
        dispatched.append(user_id)
        if user_id == str(user_a.id):
            raise ConnectionError("redis is down")

    monkeypatch.setattr("cold_email.workers.drafting.drafting.drafting_task.delay", fake_delay)

    from cold_email.workers.drafting.drafting import drafting_recovery_task

    result = drafting_recovery_task()

    # Both dispatches were attempted despite the first raising.
    assert set(dispatched) == {str(user_a.id), str(user_b.id)}
    # Only the successful one counts toward the reported total.
    assert result["users_swept"] == 1


@pytest.mark.asyncio
async def test_recovery_sweep_reclaims_stale_drafting_claim(
    async_session, sync_session_for, pending_views
):
    """A hard Celery process crash (SIGKILL, OOM, container eviction) between
    claim_pending_drafts moving a row to 'drafting' and the row finishing is
    NOT a Python exception, so drafting_task's own per-row `except Exception`
    never runs to release the claim. Without this reclaim path the row would
    sit at 'drafting' forever — invisible to pending_drafts, invisible to the
    stale-'queued' query, no error_msg, no DLQ row."""
    from datetime import UTC, datetime, timedelta

    from cold_email.database import OUTREACH_DRAFTING, OUTREACH_QUEUED, ROLE_USER, User

    user = User(email="crash-drafter@example.com", google_sub="sub-crash-drafter", role=ROLE_USER)
    async_session.add(user)
    await async_session.commit()

    outreach = await _add_queued_outreach(async_session, user.id, "CrashCo", "crash@crash.co")
    outreach.status = OUTREACH_DRAFTING
    outreach.updated_at = datetime.now(UTC) - timedelta(minutes=45)
    await async_session.commit()

    from cold_email.workers.drafting.drafting import drafting_recovery_task

    result = drafting_recovery_task()

    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_QUEUED
    assert outreach.reclaim_count == 1
    assert outreach.error_msg is not None
    assert result["reclaimed"] == 1


@pytest.mark.asyncio
async def test_recovery_sweep_leaves_recent_drafting_claim_alone(
    async_session, sync_session_for, pending_views, monkeypatch
):
    """A row claimed moments ago is a sweep genuinely in flight, not a crashed
    one. Reclaiming it anyway would put it back in 'queued' while the live
    sweep still holds it and is about to finish drafting it — recreating the
    exact double-draft the claim's compare-and-swap exists to prevent."""
    from cold_email.database import OUTREACH_DRAFTING, ROLE_USER, User

    user = User(email="in-flight@example.com", google_sub="sub-in-flight", role=ROLE_USER)
    async_session.add(user)
    await async_session.commit()

    outreach = await _add_queued_outreach(async_session, user.id, "InFlightCo", "flight@co.com")
    outreach.status = OUTREACH_DRAFTING
    # No explicit updated_at: the ORM's onupdate=func.now() stamps it "now" on
    # this very commit, same as the real claim_pending_drafts UPDATE would.
    await async_session.commit()

    dispatched = []
    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.drafting_task.delay",
        lambda uid: dispatched.append(uid),
    )

    from cold_email.workers.drafting.drafting import drafting_recovery_task

    result = drafting_recovery_task()

    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_DRAFTING
    assert outreach.reclaim_count == 0
    assert outreach.error_msg is None
    assert result["reclaimed"] == 0
    assert str(user.id) not in dispatched


@pytest.mark.asyncio
async def test_recovery_sweep_dead_letters_after_reclaim_cap(
    async_session, sync_session_for, pending_views, monkeypatch
):
    """A row that keeps crashing its worker must not be requeued forever —
    that recreates exactly the silent-infinite-retry problem the DLQ exists to
    prevent. Once it has already been reclaimed MAX_DRAFTING_RECLAIMS times,
    the next stale detection must dead-letter it via fail_outreach instead of
    handing it back to 'queued' again."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from cold_email.database import (
        OUTREACH_DRAFTING,
        OUTREACH_FAILED,
        ROLE_USER,
        DeadLetter,
        User,
    )
    from cold_email.workers.drafting.constants import DRAFTING
    from cold_email.workers.drafting.drafting import MAX_DRAFTING_RECLAIMS

    user = User(email="doomed@example.com", google_sub="sub-doomed", role=ROLE_USER)
    async_session.add(user)
    await async_session.commit()

    outreach = await _add_queued_outreach(async_session, user.id, "DoomedCo", "doom@doom.co")
    outreach.status = OUTREACH_DRAFTING
    outreach.reclaim_count = MAX_DRAFTING_RECLAIMS
    outreach.updated_at = datetime.now(UTC) - timedelta(minutes=45)
    await async_session.commit()

    dispatched = []
    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.drafting_task.delay",
        lambda uid: dispatched.append(uid),
    )

    from cold_email.workers.drafting.drafting import drafting_recovery_task

    result = drafting_recovery_task()

    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_FAILED
    assert result["dead_lettered"] == 1
    assert str(user.id) not in dispatched

    dl_rows = (
        (
            await async_session.execute(
                select(DeadLetter).where(DeadLetter.outreach_id == outreach.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(dl_rows) == 1
    assert dl_rows[0].stage == DRAFTING


@pytest.mark.asyncio
async def test_byok_users_credentials_reach_the_llm(
    async_session,
    sync_session_for,
    queued_outreach,
    admin_profile,
    admin_gmail_connected,
    byok_admin,
    monkeypatch,
):
    captured = {}
    monkeypatch.setattr(
        "cold_email.workers.drafting.helpers.generation.generate_json",
        lambda **kw: (
            captured.update(kw)
            or '{"subject":"s","company_interest":"c",'
            '"admiration_detail":"a","intro":"i","tailored_bullets":["A: b"]}'
        ),
    )
    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.create_draft", lambda creds, **kwargs: "gmail-fake"
    )

    from cold_email.workers.drafting.drafting import drafting_task

    drafting_task(str(byok_admin.id))
    assert captured["credentials"].is_byok is True


@pytest.mark.asyncio
async def test_drafting_pairs_each_users_own_credentials_and_resume(
    async_session, sync_session_for, pending_views, monkeypatch, captured_drafts
):
    """The bug this test would have caught: a single-context sweep drafted
    EVERY pending row with the FIRST owner's profile, résumé, and Gmail
    mailbox. So with two users each holding one queued row, user B's contact
    would receive an email built from user A's profile, A's résumé attached,
    sent from A's mailbox -- and B's later Approve would fail silently
    because the draft lives in A's mailbox, not B's.

    Two real users, two real profiles (distinct résumé bytes), two real Gmail
    identities, one queued row each: each user's OWN drafting_task(user_id)
    call must be paired with ITS OWN owner's creds and résumé, never the
    other user's — proven by calling the task once per user rather than a
    single sweep over both.
    """
    from cold_email.auth.crypto import encrypt
    from cold_email.database import ROLE_USER, Profile, User

    user_a = User(email="usera@example.com", google_sub="sub-usera", role=ROLE_USER)
    user_b = User(email="userb@example.com", google_sub="sub-userb", role=ROLE_USER)
    async_session.add_all([user_a, user_b])
    await async_session.commit()

    user_a.gmail_refresh_token_enc = encrypt("rt-user-a")  # noqa: S106
    user_a.gmail_sender_email = "mailbox-a@example.com"
    user_b.gmail_refresh_token_enc = encrypt("rt-user-b")  # noqa: S106
    user_b.gmail_sender_email = "mailbox-b@example.com"
    await async_session.commit()

    async_session.add_all(
        [
            Profile(
                user_id=user_a.id,
                name="User A",
                intro="I am user A.",
                experience_pool=["Acme: a thing"],
                resume_pdf=b"%PDF-1.7\n" + b"A" * 2048,
                resume_filename="user-a.pdf",
            ),
            Profile(
                user_id=user_b.id,
                name="User B",
                intro="I am user B.",
                experience_pool=["Globex: another thing"],
                resume_pdf=b"%PDF-1.7\n" + b"B" * 2048,
                resume_filename="user-b.pdf",
            ),
        ]
    )
    await async_session.commit()

    await _add_queued_outreach(async_session, user_a.id, "AcmeCo", "contact-a@acmeco.com")
    await _add_queued_outreach(async_session, user_b.id, "GlobexCo", "contact-b@globexco.com")

    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.draft_email",
        lambda row, profile, credentials=None: {
            "subject": f"Hi from {profile.name}",
            "body": "Body",
            "body_html": "<p>Body</p>",
        },
    )

    from cold_email.workers.drafting.drafting import drafting_task

    result_a = drafting_task(str(user_a.id))
    result_b = drafting_task(str(user_b.id))

    assert result_a["drafted"] == 1
    assert result_b["drafted"] == 1
    assert len(captured_drafts) == 2

    by_recipient = {call["to"]: call for call in captured_drafts}
    call_a = by_recipient["contact-a@acmeco.com"]
    call_b = by_recipient["contact-b@globexco.com"]

    assert call_a["creds"].sender_email == "mailbox-a@example.com"
    assert call_a["attachment"][1] == b"%PDF-1.7\n" + b"A" * 2048

    assert call_b["creds"].sender_email == "mailbox-b@example.com"
    assert call_b["attachment"][1] == b"%PDF-1.7\n" + b"B" * 2048

    # Never cross-paired: A's mailbox must not carry B's résumé or vice versa.
    assert call_a["creds"].sender_email != call_b["creds"].sender_email
    assert call_a["attachment"][1] != call_b["attachment"][1]


@pytest.mark.asyncio
async def test_one_users_missing_profile_does_not_block_another_users_draft(
    async_session, sync_session_for, pending_views, monkeypatch
):
    """User A has no profile at all; user B does. B's row must still draft
    when swept, A's row must stay 'queued' (recoverable by completing the
    profile, no manual DLQ retry), and neither user's skip should write a
    dead-letter row."""
    from sqlalchemy import func, select

    from cold_email.auth.crypto import encrypt
    from cold_email.database import (
        OUTREACH_DRAFTED,
        OUTREACH_QUEUED,
        ROLE_USER,
        DeadLetter,
        Profile,
        User,
    )

    user_a = User(email="noprofile@example.com", google_sub="sub-noprofile", role=ROLE_USER)
    user_b = User(email="hasprofile@example.com", google_sub="sub-hasprofile", role=ROLE_USER)
    async_session.add_all([user_a, user_b])
    await async_session.commit()

    # Only A gets Gmail connected -- irrelevant, since A's missing profile
    # must be caught first, but keeps the scenario realistic.
    user_a.gmail_refresh_token_enc = encrypt("rt-user-a")  # noqa: S106
    user_a.gmail_sender_email = "mailbox-a@example.com"
    user_b.gmail_refresh_token_enc = encrypt("rt-user-b")  # noqa: S106
    user_b.gmail_sender_email = "mailbox-b@example.com"
    await async_session.commit()

    async_session.add(
        Profile(
            user_id=user_b.id,
            name="User B",
            intro="I am user B.",
            experience_pool=["Globex: a thing"],
            resume_pdf=b"%PDF-1.7\n" + b"B" * 2048,
            resume_filename="user-b.pdf",
        )
    )
    await async_session.commit()

    outreach_a = await _add_queued_outreach(async_session, user_a.id, "NoProfileCo", "a@nop.com")
    await _add_queued_outreach(async_session, user_b.id, "HasProfileCo", "b@hap.com")

    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.draft_email",
        lambda row, profile, credentials=None: {
            "subject": "Hi",
            "body": "Body",
            "body_html": "<p>Body</p>",
        },
    )
    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.create_draft", lambda creds, **kwargs: "gmail-fake"
    )

    from cold_email.workers.drafting.drafting import drafting_task

    result_a = drafting_task(str(user_a.id))
    result_b = drafting_task(str(user_b.id))

    assert result_a == {"status": "no_profile", "drafted": 0}
    assert result_b["drafted"] == 1

    await async_session.refresh(outreach_a)
    assert outreach_a.status == OUTREACH_QUEUED

    from cold_email.database import Outreach

    outreach_b = (
        await async_session.execute(select(Outreach).where(Outreach.user_id == user_b.id))
    ).scalar_one()
    assert outreach_b.status == OUTREACH_DRAFTED

    assert (
        await async_session.execute(select(func.count()).select_from(DeadLetter))
    ).scalar_one() == 0


@pytest.mark.asyncio
async def test_two_concurrent_dispatches_draft_a_row_exactly_once(
    async_session,
    admin_user_id,
    sync_session_for,
    queued_outreach,
    admin_profile,
    admin_gmail_connected,
    monkeypatch,
    captured_drafts,
):
    """The bug this test would have caught: a row stayed 'queued' until AFTER
    the LLM call and the Gmail round-trip, so a second dispatch over the same
    queued rows (a double click, a Regenerate landing mid-sweep, the hourly
    recovery sweep) would read the same rows from pending_drafts and draft
    them a second time — two `drafts` rows and two Gmail drafts to the same
    contact.

    Simulated without real threads: both "concurrent" dispatches see the
    exact same pending_drafts snapshot (fetch_pending_drafts is pinned to
    return it unconditionally, standing in for both having already read it
    before either claimed), then drafting_task is called twice in a row.
    claim_pending_drafts's `WHERE status = 'queued'` is the compare-and-swap
    that must make the second call's claim come back empty regardless of
    how the two calls interleave.
    """
    from sqlalchemy import func, select

    import cold_email.workers.drafting.drafting as drafting_module
    from cold_email.database import Draft

    snapshot = drafting_module.fetch_pending_drafts(str(admin_user_id))
    assert len(snapshot) == 1
    monkeypatch.setattr(drafting_module, "fetch_pending_drafts", lambda user_id: snapshot)
    monkeypatch.setattr(
        drafting_module,
        "draft_email",
        lambda row, profile, credentials=None: {
            "subject": "Hi",
            "body": "Body",
            "body_html": "<p>Body</p>",
        },
    )

    result_1 = drafting_module.drafting_task(str(admin_user_id))
    result_2 = drafting_module.drafting_task(str(admin_user_id))

    assert result_1["drafted"] == 1
    assert result_2["drafted"] == 0
    assert len(captured_drafts) == 1

    await async_session.refresh(queued_outreach)
    assert queued_outreach.status == OUTREACH_DRAFTED

    draft_count = (
        await async_session.execute(
            select(func.count()).select_from(Draft).where(Draft.outreach_id == queued_outreach.id)
        )
    ).scalar_one()
    assert draft_count == 1
