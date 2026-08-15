from unittest.mock import patch

import pytest

from cold_email.database import OUTREACH_DRAFTED
from cold_email.workers.drafting.drafting import drafting_task
from cold_email.workers.shared.views import PendingDraft

OUTREACH_A = "00000000-0000-0000-0000-00000000000a"
OUTREACH_B = "00000000-0000-0000-0000-00000000000b"


def _pending_row(outreach_id, contact_email="contact@acme.com"):
    return PendingDraft(
        outreach_id=outreach_id,
        user_id="00000000-0000-0000-0000-0000000000u1",
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


def test_drafting_sweep_empty():
    """No pending outreach rows -> the sweep is a no-op returning drafted: 0."""
    with (
        patch("cold_email.workers.drafting.drafting.bridge_queue_admin_outreach", return_value=0),
        patch("cold_email.workers.drafting.drafting.fetch_pending_drafts", return_value=[]),
    ):
        result = drafting_task()
    assert result == {"status": "success", "drafted": 0}


def test_drafting_sweep_happy_path():
    """A draftable outreach row is generated, drafted in Gmail, persisted, and advanced."""
    with (
        patch("cold_email.workers.drafting.drafting.bridge_queue_admin_outreach", return_value=0),
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(OUTREACH_A)],
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
        patch("cold_email.workers.drafting.drafting.time.sleep"),
    ):
        result = drafting_task()

    assert result == {"status": "success", "drafted": 1}
    _, kwargs = mock_create.call_args
    assert kwargs["to"] == "contact@acme.com"
    assert kwargs["subject"] == "Hi"
    assert kwargs["body"] == "A specific, short note."
    assert kwargs["html"] == "<p>A specific, short note.</p>"
    assert "attachment_path" in kwargs
    assert kwargs["attachment_path"].endswith("cold_email/resume.pdf")
    mock_commit.assert_called_once()
    mock_status.assert_called_once_with(OUTREACH_A, OUTREACH_DRAFTED)


def test_drafting_skips_outreach_without_contact_email():
    """An outreach row with no contact_email is marked failed and never sent to the LLM."""
    with (
        patch("cold_email.workers.drafting.drafting.bridge_queue_admin_outreach", return_value=0),
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(OUTREACH_A, contact_email=None)],
        ),
        patch("cold_email.workers.drafting.drafting.draft_email") as mock_draft,
        patch("cold_email.workers.drafting.drafting.fail_outreach") as mock_fail,
    ):
        result = drafting_task()

    assert result == {"status": "success", "drafted": 0}
    mock_draft.assert_not_called()
    mock_fail.assert_called_once()
    assert mock_fail.call_args.args[0] == OUTREACH_A


def test_drafting_marks_empty_draft_failed():
    """An empty/malformed model draft is terminal for that outreach row."""
    with (
        patch("cold_email.workers.drafting.drafting.bridge_queue_admin_outreach", return_value=0),
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(OUTREACH_A)],
        ),
        patch("cold_email.workers.drafting.drafting.draft_email", return_value={}),
        patch("cold_email.workers.drafting.drafting.create_draft") as mock_create,
        patch("cold_email.workers.drafting.drafting.fail_outreach") as mock_fail,
        patch("cold_email.workers.drafting.drafting.time.sleep"),
    ):
        result = drafting_task()

    assert result == {"status": "success", "drafted": 0}
    mock_create.assert_not_called()
    assert mock_fail.call_args.args[0] == OUTREACH_A


def test_drafting_one_bad_outreach_does_not_abort_sweep():
    """A transient failure on one row leaves it for the next sweep; others still draft."""
    with (
        patch("cold_email.workers.drafting.drafting.bridge_queue_admin_outreach", return_value=0),
        patch(
            "cold_email.workers.drafting.drafting.fetch_pending_drafts",
            return_value=[_pending_row(OUTREACH_A), _pending_row(OUTREACH_B)],
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
        patch("cold_email.workers.drafting.drafting.time.sleep"),
    ):
        result = drafting_task()

    # Only the second row drafted; the first was left at 'queued' (no status write).
    assert result == {"status": "success", "drafted": 1}
    mock_status.assert_called_once_with(OUTREACH_B, OUTREACH_DRAFTED)


@pytest.mark.asyncio
async def test_bridge_queues_researched_companies_for_the_admin(
    async_session, admin_user_id, sync_session_for
):
    """Nothing creates outreach rows until Stack 3's pool UI, so the bridge
    preserves today's behaviour (admin drafts everything researched)."""
    from cold_email.database import (
        OUTREACH_QUEUED,
        RESEARCH_RESEARCHED,
        Company,
        CompanyContact,
        Outreach,
    )
    from cold_email.workers.drafting.drafting import bridge_queue_admin_outreach

    company = Company(company_name="Acme", research_status=RESEARCH_RESEARCHED)
    async_session.add(company)
    await async_session.commit()
    async_session.add(
        CompanyContact(company_id=company.id, email="a@acme.com", eligible=True, confidence=90)
    )
    await async_session.commit()

    assert bridge_queue_admin_outreach() == 1

    from sqlalchemy import select

    outreach = (await async_session.execute(select(Outreach))).scalar_one()
    assert outreach.user_id == admin_user_id
    assert outreach.status == OUTREACH_QUEUED


@pytest.mark.asyncio
async def test_bridge_skips_companies_without_an_eligible_contact(
    async_session, admin_user_id, sync_session_for
):
    from cold_email.database import RESEARCH_RESEARCHED, Company, CompanyContact
    from cold_email.workers.drafting.drafting import bridge_queue_admin_outreach

    company = Company(company_name="Acme", research_status=RESEARCH_RESEARCHED)
    async_session.add(company)
    await async_session.commit()
    async_session.add(CompanyContact(company_id=company.id, email="info@acme.com", eligible=False))
    await async_session.commit()

    assert bridge_queue_admin_outreach() == 0


@pytest.mark.asyncio
async def test_bridge_is_idempotent(async_session, admin_user_id, sync_session_for):
    """Runs on every 15-minute sweep; must not duplicate."""
    from cold_email.database import RESEARCH_RESEARCHED, Company, CompanyContact
    from cold_email.workers.drafting.drafting import bridge_queue_admin_outreach

    company = Company(company_name="Acme", research_status=RESEARCH_RESEARCHED)
    async_session.add(company)
    await async_session.commit()
    async_session.add(
        CompanyContact(company_id=company.id, email="a@acme.com", eligible=True, confidence=90)
    )
    await async_session.commit()

    assert bridge_queue_admin_outreach() == 1
    assert bridge_queue_admin_outreach() == 0


@pytest.mark.asyncio
async def test_bridge_picks_the_highest_confidence_contact(
    async_session, admin_user_id, sync_session_for
):
    """The bridge uses simple highest-confidence selection; least-used-with-cap
    selection is Stack 3's."""
    from cold_email.database import RESEARCH_RESEARCHED, Company, CompanyContact, Outreach
    from cold_email.workers.drafting.drafting import bridge_queue_admin_outreach

    company = Company(company_name="Acme", research_status=RESEARCH_RESEARCHED)
    async_session.add(company)
    await async_session.commit()
    async_session.add_all(
        [
            CompanyContact(
                company_id=company.id, email="low@acme.com", eligible=True, confidence=30
            ),
            CompanyContact(
                company_id=company.id, email="high@acme.com", eligible=True, confidence=95
            ),
        ]
    )
    await async_session.commit()

    bridge_queue_admin_outreach()

    from sqlalchemy import select

    outreach = (await async_session.execute(select(Outreach))).scalar_one()
    contact = (
        await async_session.execute(
            select(CompanyContact).where(CompanyContact.id == outreach.contact_id)
        )
    ).scalar_one()
    assert contact.email == "high@acme.com"


@pytest.mark.asyncio
async def test_empty_model_output_fails_only_that_outreach_row(
    async_session, admin_user_id, monkeypatch, sync_session_for, pending_views
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

    async_session.add_all(
        [
            CompanyContact(
                company_id=good_company.id,
                email="good@acme.com",
                first_name="Ann",
                eligible=True,
                confidence=90,
            ),
            CompanyContact(
                company_id=bad_company.id,
                email="bad@globex.com",
                first_name="Bea",
                eligible=True,
                confidence=90,
            ),
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

    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.draft_email",
        lambda row: (
            {} if row.contact_email == "bad@globex.com" else {"subject": "Hi", "body": "Body"}
        ),
    )
    monkeypatch.setattr("cold_email.workers.drafting.drafting.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        "cold_email.workers.drafting.drafting.create_draft", lambda **kwargs: "gmail-1"
    )

    from cold_email.workers.drafting.drafting import drafting_task

    result = drafting_task()

    assert result["drafted"] == 1

    from sqlalchemy import select

    outreach_rows = (await async_session.execute(select(Outreach))).scalars().all()
    assert {o.status for o in outreach_rows} == {OUTREACH_DRAFTED, OUTREACH_FAILED}

    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert dl.outreach_id is not None
    assert dl.company_id is None
    assert dl.stage == "drafting"
