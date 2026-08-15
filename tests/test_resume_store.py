import pytest

from cold_email.resume_store import (
    MAX_RESUME_BYTES,
    ResumeInvalid,
    delete_resume,
    get_resume,
    put_resume,
    validate_resume,
)

VALID_PDF = b"%PDF-1.7\n" + b"x" * 2048


@pytest.fixture
async def profile(async_session, admin_user_id):
    from cold_email.database import Profile

    p = Profile(user_id=admin_user_id, name="A", intro="i")
    async_session.add(p)
    await async_session.commit()
    return p


def test_validate_accepts_a_real_pdf():
    validate_resume("cv.pdf", VALID_PDF)  # does not raise


def test_validate_rejects_oversize():
    """Cloud SQL disk grows but never shrinks, so an unbounded upload path
    permanently inflates the instance and every backup."""
    with pytest.raises(ResumeInvalid, match="too large"):
        validate_resume("cv.pdf", b"%PDF-" + b"x" * MAX_RESUME_BYTES)


def test_validate_rejects_non_pdf_magic_bytes():
    """Magic bytes, not the extension or Content-Type — both are
    attacker-controlled, and pypdf on arbitrary bytes is a parser you do not
    want to hand untrusted input."""
    with pytest.raises(ResumeInvalid, match="not a PDF"):
        validate_resume("cv.pdf", b"MZ\x90\x00 this is an exe")


def test_validate_rejects_empty():
    with pytest.raises(ResumeInvalid):
        validate_resume("cv.pdf", b"")


@pytest.mark.asyncio
async def test_round_trip(async_session, profile):
    await put_resume(async_session, profile.user_id, "cv.pdf", VALID_PDF)
    filename, data = await get_resume(async_session, profile.user_id)
    assert filename == "cv.pdf"
    assert data == VALID_PDF


@pytest.mark.asyncio
async def test_get_returns_none_when_absent(async_session, profile):
    assert await get_resume(async_session, profile.user_id) is None


@pytest.mark.asyncio
async def test_delete_clears_bytes_but_keeps_the_profile(async_session, profile):
    await put_resume(async_session, profile.user_id, "cv.pdf", VALID_PDF)
    await delete_resume(async_session, profile.user_id)

    assert await get_resume(async_session, profile.user_id) is None
    await async_session.refresh(profile)
    assert profile.name == "A"  # profile fields survive


@pytest.mark.asyncio
async def test_put_validates_before_storing(async_session, profile):
    with pytest.raises(ResumeInvalid):
        await put_resume(async_session, profile.user_id, "bad.pdf", b"not a pdf")
    assert await get_resume(async_session, profile.user_id) is None
