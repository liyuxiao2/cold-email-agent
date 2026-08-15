"""Contact selection — pure logic over counts, no network.

Deterministic selection was chosen over random precisely so these properties are
assertable rather than statistical.
"""

import pytest

from cold_email.contact_selection import select_contact
from cold_email.database import Company, CompanyContact, Outreach


@pytest.fixture
async def company(async_session, pending_views):
    # pending_views: select_contact reads the available_contacts view, which
    # create_all-based async_session does not provision (see conftest's
    # pending_views docstring) — every test in this file needs it.
    c = Company(company_name="Acme", research_status="researched")
    async_session.add(c)
    await async_session.commit()
    return c


async def _contact(session, company, email, *, confidence=50, eligible=True, is_founder=False):
    contact = CompanyContact(
        company_id=company.id,
        email=email,
        confidence=confidence,
        eligible=eligible,
        is_founder=is_founder,
    )
    session.add(contact)
    await session.commit()
    return contact


async def _use(session, contact, user_id, company):
    """Record that someone emailed this contact."""
    session.add(
        Outreach(user_id=user_id, company_id=company.id, contact_id=contact.id, status="sent")
    )
    await session.commit()


@pytest.mark.asyncio
async def test_picks_the_only_eligible_contact(async_session, company):
    contact = await _contact(async_session, company, "a@acme.com")
    assert await select_contact(async_session, company.id, cap=3) == contact.id


@pytest.mark.asyncio
async def test_ignores_ineligible_contacts_even_at_zero_use(async_session, company):
    await _contact(async_session, company, "info@acme.com", eligible=False)
    assert await select_contact(async_session, company.id, cap=3) is None


@pytest.mark.asyncio
async def test_picks_the_least_used(async_session, company, admin_user_id, extra_users):
    used = await _contact(async_session, company, "used@acme.com", confidence=95)
    fresh = await _contact(async_session, company, "fresh@acme.com", confidence=50)
    await _use(async_session, used, admin_user_id, company)

    # Higher confidence loses to lower use_count — spreading beats deliverability.
    assert await select_contact(async_session, company.id, cap=3) == fresh.id


@pytest.mark.asyncio
async def test_confidence_breaks_a_use_count_tie(async_session, company):
    await _contact(async_session, company, "low@acme.com", confidence=30)
    high = await _contact(async_session, company, "high@acme.com", confidence=95)
    assert await select_contact(async_session, company.id, cap=3) == high.id


@pytest.mark.asyncio
async def test_is_founder_breaks_a_confidence_tie(async_session, company):
    await _contact(async_session, company, "eng@acme.com", confidence=80, is_founder=False)
    founder = await _contact(async_session, company, "f@acme.com", confidence=80, is_founder=True)
    assert await select_contact(async_session, company.id, cap=3) == founder.id


@pytest.mark.asyncio
async def test_founder_preference_never_outranks_spreading(async_session, company, admin_user_id):
    """is_founder sits BELOW use_count. Above it, volume re-concentrates on the
    exact address contact spreading exists to protect."""
    founder = await _contact(async_session, company, "f@acme.com", confidence=95, is_founder=True)
    other = await _contact(async_session, company, "cto@acme.com", confidence=90)
    await _use(async_session, founder, admin_user_id, company)

    assert await select_contact(async_session, company.id, cap=3) == other.id


@pytest.mark.asyncio
async def test_returns_none_when_every_contact_is_capped(
    async_session, company, admin_user_id, extra_users
):
    contact = await _contact(async_session, company, "a@acme.com")
    for user_id in [admin_user_id, *extra_users[:2]]:
        await _use(async_session, contact, user_id, company)

    assert await select_contact(async_session, company.id, cap=3) is None


@pytest.mark.asyncio
async def test_use_count_spans_all_users(async_session, company, extra_users):
    """A per-caller count would let 10 users each email the same founder once."""
    contact = await _contact(async_session, company, "a@acme.com")
    for user_id in extra_users[:3]:
        await _use(async_session, contact, user_id, company)

    assert await select_contact(async_session, company.id, cap=3) is None


@pytest.mark.asyncio
async def test_sequential_selections_round_robin(async_session, company, extra_users):
    """The core property: consecutive users get different addresses."""
    a = await _contact(async_session, company, "a@acme.com", confidence=90)
    b = await _contact(async_session, company, "b@acme.com", confidence=90)
    c = await _contact(async_session, company, "c@acme.com", confidence=90)

    picked = []
    for user_id in extra_users[:3]:
        chosen = await select_contact(async_session, company.id, cap=3)
        picked.append(chosen)
        contact = await async_session.get(CompanyContact, chosen)
        await _use(async_session, contact, user_id, company)

    assert sorted(picked) == sorted([a.id, b.id, c.id])
    assert len(set(picked)) == 3


@pytest.mark.asyncio
async def test_selection_is_deterministic_for_identical_contacts(async_session, company):
    """Total ordering via `id ASC`. Without it, two equal rows make the test
    flaky in a way that looks like a selection bug."""
    await _contact(async_session, company, "a@acme.com", confidence=50)
    await _contact(async_session, company, "b@acme.com", confidence=50)

    first = await select_contact(async_session, company.id, cap=3)
    for _ in range(5):
        assert await select_contact(async_session, company.id, cap=3) == first


@pytest.mark.asyncio
async def test_no_contacts_at_all(async_session, company):
    assert await select_contact(async_session, company.id, cap=3) is None
