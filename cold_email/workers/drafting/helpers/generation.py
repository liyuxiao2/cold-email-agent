"""Generation helpers for the drafting worker.

All LLM I/O: turn a pending_drafts row into a {subject, body} dict via Gemini.
Mirrors research/helpers/extraction.py's call_gemini / parse pattern.
"""

import json
import logging

from google import genai

from cold_email.config import settings
from cold_email.prompts.email_draft import (
    EMAIL_DRAFT_SYSTEM,
    EMAIL_DRAFT_TOOL,
    build_email_draft_messages,
)
from cold_email.workers.drafting.constants import (
    DEFAULT_FOUNDER_TITLE,
    JSON_BLOCK_END_MARKER,
    JSON_BLOCK_START_MARKER,
    MODEL_NAME,
)
from cold_email.workers.drafting.helpers.db_helpers import PendingDraft

logger = logging.getLogger(__name__)


def draft_email(row: PendingDraft) -> dict:
    """Produce a {subject, body} draft for a lead ({} if the model returns nothing).

    Composes the two LLM steps so the worker calls one thing instead of wiring
    generate → parse itself.
    """
    return parse_email_response(generate_email(row))


def generate_email(row: PendingDraft):
    """Send a pending_drafts row to Gemini and return the raw model response.`

    `row` is a PendingDraft from the pending_drafts view: it carries company_name,
    founder_name, founder_email and the joined research (tech_stack/recent_news/hook).
    Sender identity comes from settings.
    """
    client = genai.Client(api_key=settings.gemini_api_key)
    model = client.models.get(model=MODEL_NAME)
    messages = build_email_draft_messages(
        sender_name=settings.sender_name,
        sender_role=settings.sender_role,
        sender_company=settings.sender_company,
        founder_name=row.founder_name or "there",
        founder_title=DEFAULT_FOUNDER_TITLE,
        company_name=row.company_name,
        tech_stack=row.tech_stack or [],
        recent_news=row.recent_news or "",
        hook=row.hook or "",
    )
    return model.generate_content(
        messages,
        config={
            "system_instruction": EMAIL_DRAFT_SYSTEM,
            "tools": [EMAIL_DRAFT_TOOL],
        },
    )


def parse_email_response(response) -> dict:
    """Parse {subject, body} from a Gemini response, stripping any ```json fence.

    Returns {} if the response is missing or malformed — the caller treats an
    empty/incomplete draft as a terminal failure for that lead.
    """
    if not response.text:
        return {}

    raw_json = response.text.strip()
    if raw_json.startswith(JSON_BLOCK_START_MARKER) and raw_json.endswith(
        JSON_BLOCK_END_MARKER
    ):
        raw_json = raw_json[len(JSON_BLOCK_START_MARKER) : -len(JSON_BLOCK_END_MARKER)].strip()

    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse email draft JSON: {raw_json}")
        return {}
