import pytest
from sqlalchemy import select

from cold_email.database import ROLE_ADMIN, User
from scripts.seed_admin import seed_admin


@pytest.mark.asyncio
async def test_seeds_an_admin_when_absent(async_session, monkeypatch):
    monkeypatch.setattr("cold_email.config.settings.admin_email", "boss@example.com")
    await seed_admin(async_session)

    user = (
        await async_session.execute(select(User).where(User.email == "boss@example.com"))
    ).scalar_one()
    assert user.role == ROLE_ADMIN
    assert user.google_sub is None


@pytest.mark.asyncio
async def test_is_idempotent(async_session, monkeypatch):
    """start.sh runs this on every boot, so a second call must not duplicate."""
    monkeypatch.setattr("cold_email.config.settings.admin_email", "boss@example.com")
    await seed_admin(async_session)
    await seed_admin(async_session)

    users = (await async_session.execute(select(User))).scalars().all()
    assert len(users) == 1


@pytest.mark.asyncio
async def test_promotes_an_existing_user(async_session, monkeypatch):
    async_session.add(User(email="boss@example.com", google_sub="sub-x"))
    await async_session.commit()

    monkeypatch.setattr("cold_email.config.settings.admin_email", "boss@example.com")
    await seed_admin(async_session)

    user = (
        await async_session.execute(select(User).where(User.email == "boss@example.com"))
    ).scalar_one()
    assert user.role == ROLE_ADMIN
    assert user.google_sub == "sub-x"


@pytest.mark.asyncio
async def test_no_admin_email_is_a_noop(async_session, monkeypatch):
    monkeypatch.setattr("cold_email.config.settings.admin_email", "")
    await seed_admin(async_session)
    assert (await async_session.execute(select(User))).scalars().all() == []
