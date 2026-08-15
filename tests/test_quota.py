from datetime import UTC, datetime, timedelta

import pytest

from cold_email.quota import check, period_start, usage


def test_period_starts_at_the_first_of_the_month_utc():
    start = period_start(datetime(2026, 8, 14, 17, 30, tzinfo=UTC))
    assert start == datetime(2026, 8, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_usage_counts_only_this_period(async_session, admin_user, company_factory):
    from cold_email.database import Outreach

    now = datetime.now(UTC)
    async_session.add_all(
        [
            Outreach(
                user_id=admin_user.id,
                company_id=(await company_factory()).id,
                status="sent",
                created_at=now,
            ),
            Outreach(
                user_id=admin_user.id,
                company_id=(await company_factory()).id,
                status="sent",
                created_at=period_start(now) - timedelta(days=1),
            ),
        ]
    )
    await async_session.commit()

    used, _ = await usage(async_session, admin_user)
    assert used == 1


@pytest.mark.asyncio
async def test_usage_counts_only_the_caller(
    async_session, admin_user, extra_users, company_factory
):
    from cold_email.database import Outreach

    async_session.add(
        Outreach(user_id=extra_users[0], company_id=(await company_factory()).id, status="sent")
    )
    await async_session.commit()

    used, _ = await usage(async_session, admin_user)
    assert used == 0


@pytest.mark.asyncio
async def test_usage_counts_creations_not_sends(async_session, admin_user, company_factory):
    """The LLM call is the cost and it happens at drafting. A user who drafts 100
    and approves 3 has spent 100 units of the rationed thing."""
    from cold_email.database import Outreach

    async_session.add_all(
        [
            Outreach(
                user_id=admin_user.id, company_id=(await company_factory()).id, status="queued"
            ),
            Outreach(
                user_id=admin_user.id, company_id=(await company_factory()).id, status="rejected"
            ),
        ]
    )
    await async_session.commit()

    used, _ = await usage(async_session, admin_user)
    assert used == 2


@pytest.mark.asyncio
async def test_check_clamps_to_the_remaining_allowance(async_session, admin_user):
    admin_user.monthly_draft_quota = 5
    await async_session.commit()
    assert await check(async_session, admin_user, requested=20) == 5


@pytest.mark.asyncio
async def test_check_returns_zero_when_exhausted(async_session, admin_user, company_factory):
    from cold_email.database import Outreach

    admin_user.monthly_draft_quota = 1
    async_session.add(
        Outreach(user_id=admin_user.id, company_id=(await company_factory()).id, status="sent")
    )
    await async_session.commit()

    assert await check(async_session, admin_user, requested=5) == 0


@pytest.mark.asyncio
async def test_byok_users_bypass_the_quota(async_session, admin_user):
    from cold_email.auth.crypto import encrypt

    admin_user.monthly_draft_quota = 1
    admin_user.llm_api_key_enc = encrypt("gsk_theirs")
    admin_user.llm_provider = "groq"
    await async_session.commit()

    assert await check(async_session, admin_user, requested=500) == 500
