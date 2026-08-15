"""Per-user sender profile and résumé.

Every route is scoped to the calling user via get_current_user, so there is no
user_id in any path — a user cannot address another user's profile at all.
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.auth.deps import get_current_user
from cold_email.database import Profile, User, get_async_session
from cold_email.profile_extract import ResumeUnreadable, extract_text, suggest_profile
from cold_email.resume_store import (
    ResumeInvalid,
    delete_resume,
    get_resume,
    put_resume,
    validate_resume,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1)
    intro: str = Field(min_length=1)
    linkedin: str | None = None
    github: str | None = None
    website: str | None = None
    experience_pool: list[str] = Field(default_factory=list)
    company_links: dict[str, str] = Field(default_factory=dict)

    @field_validator("experience_pool")
    @classmethod
    def bullets_need_a_label(cls, value: list[str]) -> list[str]:
        """_bullet_md partitions on ': ' to build bold-label bullets. A bullet
        without it silently renders unlabelled and unlinked."""
        bad = [b for b in value if ": " not in b]
        if bad:
            raise ValueError(f"Bullets must be 'Label: achievement'. Offending: {bad}")
        return value


def _serialize(profile: Profile) -> dict:
    """Profile fields for the client. Deliberately omits resume_pdf."""
    return {
        "name": profile.name,
        "intro": profile.intro,
        "linkedin": profile.linkedin,
        "github": profile.github,
        "website": profile.website,
        "experience_pool": profile.experience_pool or [],
        "company_links": profile.company_links or {},
        "has_resume": profile.resume_pdf is not None,
        "resume_filename": profile.resume_filename,
    }


@router.get("")
async def get_profile(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """The caller's profile, or 404 if they have not created one."""
    profile = await session.get(Profile, user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile yet")
    return _serialize(profile)


@router.put("")
async def upsert_profile(
    payload: ProfileUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Create or replace the caller's profile fields (never the résumé bytes)."""
    profile = await session.get(Profile, user.id)
    if profile is None:
        profile = Profile(user_id=user.id, name=payload.name, intro=payload.intro)
        session.add(profile)

    for field in (
        "name",
        "intro",
        "linkedin",
        "github",
        "website",
        "experience_pool",
        "company_links",
    ):
        setattr(profile, field, getattr(payload, field))

    await session.commit()
    await session.refresh(profile)
    return _serialize(profile)


@router.post("/resume")
async def upload_resume(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Store a résumé and return a SUGGESTED profile for the user to review.

    Order matters: validate, parse, THEN store. A PDF pypdf cannot read is
    rejected without leaving unusable bytes in the database.
    """
    data = await file.read()

    try:
        validate_resume(file.filename or "resume.pdf", data)
    except ResumeInvalid as exc:
        if "too large" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc

    try:
        resume_text = extract_text(data)
    except ResumeUnreadable as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    profile = await session.get(Profile, user.id)
    if profile is None:
        # A placeholder so the bytes have somewhere to live during onboarding;
        # the user's confirmed values arrive via PUT /api/profile.
        profile = Profile(user_id=user.id, name=user.name or user.email, intro="")
        session.add(profile)
        await session.commit()

    await put_resume(session, user.id, file.filename or "resume.pdf", data)
    profile.resume_text = resume_text
    await session.commit()

    try:
        suggested = suggest_profile(resume_text)
    except Exception as exc:
        # The bytes ARE stored: the upload succeeded, only the suggestion
        # failed. Discarding a 5MB upload the user just waited for would be
        # gratuitous — they can retry parsing without re-uploading.
        logger.error(f"Profile suggestion failed for user {user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Résumé stored, but we couldn't extract a profile. Please retry.",
        ) from exc

    return {"stored": True, "suggested": suggested}


@router.get("/resume")
async def download_resume(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Download the caller's own résumé."""
    result = await get_resume(session, user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="No résumé stored")
    filename, data = result
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/resume")
async def remove_resume(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Clear the caller's résumé, keeping their profile fields."""
    await delete_resume(session, user.id)
    return {"success": True}
