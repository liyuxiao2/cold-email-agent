import pytest

from cold_email.database import (
    OUTREACH_QUEUED,
    RESEARCH_FOUND,
    Company,
    CompanyContact,
    Outreach,
)


def test_lead_model_is_gone():
    """The rename must be loud: any missed import should fail at import time,
    not silently read a stale table."""
    import cold_email.database as db

    assert not hasattr(db, "Lead")


@pytest.mark.asyncio
async def test_company_defaults_to_found(async_session):
    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()
    assert company.research_status == RESEARCH_FOUND


@pytest.mark.asyncio
async def test_contact_cascades_from_company(async_session):
    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()
    async_session.add(CompanyContact(company_id=company.id, email="a@acme.com"))
    await async_session.commit()

    await async_session.delete(company)
    await async_session.commit()

    from sqlalchemy import func, select

    assert (
        await async_session.execute(select(func.count()).select_from(CompanyContact))
    ).scalar_one() == 0


@pytest.mark.asyncio
async def test_outreach_defaults_to_queued(async_session, admin_user_id):
    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()

    outreach = Outreach(user_id=admin_user_id, company_id=company.id)
    async_session.add(outreach)
    await async_session.commit()
    assert outreach.status == OUTREACH_QUEUED
    assert outreach.scheduled_send_at is None


@pytest.mark.asyncio
async def test_deleting_a_contact_preserves_outreach_history(async_session, admin_user_id):
    """SET NULL, not CASCADE: losing the record that an email was sent would
    let the same user re-email the same person."""
    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()

    contact = CompanyContact(company_id=company.id, email="a@acme.com", eligible=True)
    async_session.add(contact)
    await async_session.commit()

    outreach = Outreach(user_id=admin_user_id, company_id=company.id, contact_id=contact.id)
    async_session.add(outreach)
    await async_session.commit()

    await async_session.delete(contact)
    await async_session.commit()
    await async_session.refresh(outreach)

    assert outreach.id is not None
    assert outreach.contact_id is None
