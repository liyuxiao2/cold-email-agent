import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cold_email.database import ROLE_ADMIN, ROLE_USER, User


@pytest.mark.asyncio
async def test_defaults_to_user_role(async_session):
    user = User(email="a@example.com", google_sub="sub-a")
    async_session.add(user)
    await async_session.commit()
    assert user.role == ROLE_USER


@pytest.mark.asyncio
async def test_email_is_unique(async_session):
    async_session.add(User(email="dup@example.com", google_sub="sub-1"))
    await async_session.commit()
    async_session.add(User(email="dup@example.com", google_sub="sub-2"))
    with pytest.raises(IntegrityError):
        await async_session.commit()


@pytest.mark.asyncio
async def test_google_sub_may_be_null_for_a_seeded_admin(async_session):
    """The admin row is seeded by email before that person ever signs in, so
    google_sub must be nullable and filled on first login."""
    admin = User(email="admin@example.com", role=ROLE_ADMIN, google_sub=None)
    async_session.add(admin)
    await async_session.commit()

    found = (
        await async_session.execute(select(User).where(User.email == "admin@example.com"))
    ).scalar_one()
    assert found.google_sub is None
    assert found.role == ROLE_ADMIN


@pytest.mark.asyncio
async def test_refresh_token_column_stores_bytes(async_session):
    user = User(email="b@example.com", google_sub="sub-b", gmail_refresh_token_enc=b"ciphertext")
    async_session.add(user)
    await async_session.commit()
    assert user.gmail_refresh_token_enc == b"ciphertext"
