"""Résumé PDF → text → suggested profile.

Two steps, deliberately separate: extraction can fail for reasons the user can
act on (a scanned PDF), while the LLM step can fail transiently. Splitting them
lets the route return a precise status for each.
"""

import io
import logging

from pypdf import PdfReader

from cold_email.prompts.resume_profile import (
    RESUME_PROFILE_SYSTEM,
    ResumeProfile,
    build_resume_profile_prompt,
)
from cold_email.workers.shared.json_parsing import parse_fenced_json
from cold_email.workers.shared.llm import generate_json

logger = logging.getLogger(__name__)

# Below this, the PDF is almost certainly a scan or an image export. Handing
# near-empty text to the LLM yields a confidently fabricated profile — a far
# worse outcome than an error message telling the user to upload a text PDF.
MIN_EXTRACTED_CHARS = 100


class ResumeUnreadable(ValueError):
    """The PDF is corrupt, or carries no extractable text."""


def extract_text(pdf_bytes: bytes) -> str:
    """Extract text from every page of a PDF."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as exc:
        raise ResumeUnreadable(f"Could not parse the PDF: {exc}") from exc

    if len(text) < MIN_EXTRACTED_CHARS:
        raise ResumeUnreadable(
            "We couldn't read text from this PDF; it may be a scan or an image export. "
            "Please upload a text-based PDF."
        )
    return text


def suggest_profile(resume_text: str) -> dict:
    """Ask the LLM for a suggested profile. Provider fallback is inside generate_json."""
    if len(resume_text) < MIN_EXTRACTED_CHARS:
        raise ResumeUnreadable("Résumé text is too short to extract a profile")

    raw = generate_json(
        system=RESUME_PROFILE_SYSTEM,
        prompt=build_resume_profile_prompt(resume_text),
        schema=ResumeProfile,
    )
    suggestion = parse_fenced_json(raw)
    if not suggestion:
        raise ResumeUnreadable("The model returned no usable profile")

    # _bullet_md partitions on ': ' to build bold-label bullets. Drop entries
    # missing the separator rather than shipping a bullet with no label.
    suggestion["experience_pool"] = [
        bullet for bullet in suggestion.get("experience_pool", []) if ": " in bullet
    ]
    return suggestion
