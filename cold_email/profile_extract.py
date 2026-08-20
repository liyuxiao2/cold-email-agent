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

# Above this, the "PDF" is not a résumé — it's a report, a book, or a dense
# text dump. Even a long, dense multi-page CV (5-10 pages of small type) tops
# out well under this; ~20,000 chars is roughly 3,000-4,000 words, generous
# for a résumé but nowhere near book length. The cap matters beyond the LLM
# call itself: `profile.resume_text` is committed BEFORE suggest_profile ever
# runs, so an unbounded value doesn't just fail once — it lands in
# `effective_resume_text` and gets fed into EVERY future drafting prompt for
# EVERY lead, forever (see `cold_email.sender_profile.SenderProfile`).
MAX_EXTRACTED_CHARS = 20_000


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

    if len(text) > MAX_EXTRACTED_CHARS:
        logger.warning(
            f"Extracted résumé text truncated from {len(text)} to {MAX_EXTRACTED_CHARS} chars"
        )
        text = text[:MAX_EXTRACTED_CHARS]

    return text


def _links_to_dict(raw) -> dict[str, str]:
    """Fold the model's company_links into the {label: url} dict the profile stores.

    Accepts both shapes on purpose. Gemini is schema-bound and returns the
    [{label, url}] list ResumeProfile declares, but Groq has no schema binding —
    it gets the JSON schema injected into its system prompt — so it can still
    answer with a bare {label: url} object. Entries missing either half are
    dropped rather than stored as a half-link that renders a bold label
    pointing nowhere.
    """
    if isinstance(raw, dict):
        return {
            k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str) and k and v
        }
    if not isinstance(raw, list):
        return {}
    links: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        label, url = entry.get("label"), entry.get("url")
        if isinstance(label, str) and isinstance(url, str) and label and url:
            links[label] = url
    return links


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
    suggestion["company_links"] = _links_to_dict(suggestion.get("company_links"))
    return suggestion
