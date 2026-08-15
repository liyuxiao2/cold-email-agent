"""The entire read/write surface for résumé PDF bytes.

Stored as `bytea` on the profile row rather than in GCS. At ~400KB per user the
dollar difference is under $1/month either way; bytea wins because the profile
row and the PDF commit in ONE transaction. With GCS they are two systems, and a
crash between the blob write and the row commit leaves an orphan file whose
reconciliation you own.

Everything goes through get_resume / put_resume so a future GCS migration is one
implementation swap plus a backfill — not a hunt through the drafting worker.
Revisit at ~5GB total, or when multi-file/versioned résumés appear.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from cold_email.database import Profile

logger = logging.getLogger(__name__)

MAX_RESUME_BYTES = 5 * 1024 * 1024  # 5 MB
PDF_MAGIC = b"%PDF-"


class ResumeInvalid(ValueError):
    """The uploaded file is not a PDF we are willing to store."""


def validate_resume(filename: str, data: bytes) -> None:
    """Reject anything we should not store or hand to pypdf.

    Checks the magic bytes rather than the extension or Content-Type: both are
    attacker-controlled, and pypdf parsing arbitrary bytes is a liability.
    """
    if not data:
        raise ResumeInvalid("File is empty")
    if len(data) > MAX_RESUME_BYTES:
        raise ResumeInvalid(
            f"File is too large ({len(data)} bytes); the limit is {MAX_RESUME_BYTES}"
        )
    if not data.startswith(PDF_MAGIC):
        raise ResumeInvalid("File is not a PDF")


async def put_resume(session: AsyncSession, user_id: uuid.UUID, filename: str, data: bytes) -> None:
    """Validate then store a résumé on the user's profile row."""
    validate_resume(filename, data)

    profile = await session.get(Profile, user_id)
    if profile is None:
        raise ResumeInvalid("No profile exists for this user")

    profile.resume_pdf = data
    profile.resume_filename = filename
    await session.commit()
    # Log the size, never the bytes.
    logger.info(f"Stored résumé for user {user_id} ({len(data)} bytes)")


async def get_resume(session: AsyncSession, user_id: uuid.UUID) -> tuple[str, bytes] | None:
    """Return (filename, bytes), or None when the user has no résumé."""
    profile = await session.get(Profile, user_id)
    if profile is None or profile.resume_pdf is None:
        return None
    return profile.resume_filename or "resume.pdf", bytes(profile.resume_pdf)


async def delete_resume(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Clear the bytes, keeping the profile fields intact."""
    profile = await session.get(Profile, user_id)
    if profile is None:
        return
    profile.resume_pdf = None
    profile.resume_filename = None
    await session.commit()


def get_resume_sync(session, user_id: uuid.UUID) -> tuple[str, bytes] | None:
    """Sync variant for Celery workers.

    Duplicated rather than shared because the async and sync SQLAlchemy sessions
    have genuinely different APIs; a shim would be more code than these 4 lines.
    """
    profile = session.get(Profile, user_id)
    if profile is None or profile.resume_pdf is None:
        return None
    return profile.resume_filename or "resume.pdf", bytes(profile.resume_pdf)
