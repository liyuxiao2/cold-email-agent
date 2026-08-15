import pytest

from cold_email.database import Profile


@pytest.mark.asyncio
async def test_one_profile_per_user(async_session, admin_user_id):
    """user_id is the PRIMARY KEY, so uniqueness is structural rather than a
    separate constraint on a surrogate id."""
    from sqlalchemy.exc import IntegrityError

    async_session.add(Profile(user_id=admin_user_id, name="A", intro="i"))
    await async_session.commit()

    async_session.add(Profile(user_id=admin_user_id, name="B", intro="j"))
    with pytest.raises(IntegrityError):
        await async_session.commit()


@pytest.mark.asyncio
async def test_json_fields_default_to_empty(async_session, admin_user_id):
    profile = Profile(user_id=admin_user_id, name="A", intro="i")
    async_session.add(profile)
    await async_session.commit()
    await async_session.refresh(profile)
    assert profile.experience_pool == []
    assert profile.company_links == {}


@pytest.mark.asyncio
async def test_stores_pdf_bytes(async_session, admin_user_id):
    profile = Profile(
        user_id=admin_user_id,
        name="A",
        intro="i",
        resume_pdf=b"%PDF-1.7 fake",
        resume_filename="cv.pdf",
    )
    async_session.add(profile)
    await async_session.commit()
    assert profile.resume_pdf.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_cascades_when_the_user_is_deleted(async_session, admin_user_id):
    from sqlalchemy import func, select

    from cold_email.database import User

    async_session.add(Profile(user_id=admin_user_id, name="A", intro="i"))
    await async_session.commit()

    await async_session.delete(await async_session.get(User, admin_user_id))
    await async_session.commit()

    assert (
        await async_session.execute(select(func.count()).select_from(Profile))
    ).scalar_one() == 0
