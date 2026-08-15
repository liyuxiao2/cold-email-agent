import pytest
from sqlalchemy import select

from cold_email.database import (
    OUTREACH_FAILED,
    RESEARCH_FAILED,
    Company,
    DeadLetter,
    Outreach,
)


@pytest.mark.asyncio
async def test_fail_company_marks_and_dead_letters(async_session, monkeypatch, sync_session_for):
    from cold_email.workers.shared.errors import fail_company

    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()

    fail_company(
        str(company.id),
        "No eligible contacts found (Hunter)",
        stage="research",
        task_name="research_task",
    )

    await async_session.refresh(company)
    assert company.research_status == RESEARCH_FAILED

    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert dl.company_id == company.id
    assert dl.outreach_id is None
    assert dl.stage == "research"


@pytest.mark.asyncio
async def test_fail_outreach_marks_and_dead_letters(async_session, admin_user_id, sync_session_for):
    from cold_email.workers.shared.errors import fail_outreach

    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()
    outreach = Outreach(user_id=admin_user_id, company_id=company.id)
    async_session.add(outreach)
    await async_session.commit()

    fail_outreach(
        str(outreach.id), "Empty model output", stage="drafting", task_name="drafting_task"
    )

    await async_session.refresh(outreach)
    assert outreach.status == OUTREACH_FAILED

    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert dl.outreach_id == outreach.id
    assert dl.company_id is None


def test_pending_draft_carries_contact_fields():
    """The greeting must come from the contact, not the company's founder."""
    from cold_email.workers.shared.views import PendingDraft

    fields = PendingDraft.__dataclass_fields__
    for name in (
        "outreach_id",
        "user_id",
        "contact_email",
        "contact_first_name",
        "contact_position",
    ):
        assert name in fields
    assert "lead_id" not in fields
    assert "founder_email" not in fields


def test_pending_send_uses_contact_email():
    from cold_email.workers.shared.views import PendingSend

    fields = PendingSend.__dataclass_fields__
    assert "contact_email" in fields
    assert "user_id" in fields
    assert "lead_id" not in fields
    assert "founder_email" not in fields
