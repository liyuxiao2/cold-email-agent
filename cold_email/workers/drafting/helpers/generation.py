"""Generation helpers for the drafting worker.

Pipeline: pending_drafts row → LLM fills the contextual slots (EmailDraftContext,
via the provider-agnostic generate_json layer) → assemble_email fills the fixed
template and renders HTML → {subject, body, body_html}. The template (not the
LLM) owns structure/tone.
"""

import json
import logging

from cold_email.prompts.email_draft import (
    EMAIL_DRAFT_SYSTEM,
    EmailDraftContext,
    build_email_draft_messages,
)
from cold_email.prompts.email_template import TEMPLATE, fill_template
from cold_email.sender_profile import PROFILE, SenderProfile
from cold_email.workers.drafting.constants import (
    JSON_BLOCK_END_MARKER,
    JSON_BLOCK_START_MARKER,
)
from cold_email.workers.drafting.helpers.html_builder import (
    markdown_to_html,
    plain_text_fallback,
)
from cold_email.workers.shared.llm import generate_json
from cold_email.workers.shared.views import PendingDraft

logger = logging.getLogger(__name__)

# Context fields required to assemble a complete email; missing any → terminal.
_REQUIRED = ("subject", "company_interest", "admiration_detail", "tailored_bullets")


def draft_email(row: PendingDraft) -> dict:
    """Produce a {subject, body, body_html} draft for a lead ({} if unusable)."""
    context = parse_email_response(generate_email(row))
    if not context:
        return {}
    return assemble_email(context, row, PROFILE)


def generate_email(row: PendingDraft) -> str:
    """Ask the LLM for the contextual slots; returns raw JSON text.

    Provider/model fallback is handled inside generate_json.
    """
    tech_stack = [row.tech_stack] if isinstance(row.tech_stack, str) else (row.tech_stack or [])
    messages = build_email_draft_messages(
        founder_name=row.founder_name or "there",
        company_name=row.company_name,
        tech_stack=tech_stack,
        recent_news=row.recent_news or "",
        hook=row.hook or "",
        experience_pool=PROFILE.experience_pool,
    )
    return generate_json(system=EMAIL_DRAFT_SYSTEM, prompt=messages, schema=EmailDraftContext)


def parse_email_response(raw: str) -> dict:
    """Parse the contextual-slots JSON, stripping any ```json fence. {} if malformed."""
    if not raw:
        return {}
    raw_json = raw.strip()
    if raw_json.startswith(JSON_BLOCK_START_MARKER) and raw_json.endswith(JSON_BLOCK_END_MARKER):
        raw_json = raw_json[len(JSON_BLOCK_START_MARKER) : -len(JSON_BLOCK_END_MARKER)].strip()
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse email draft JSON: {raw_json}")
        return {}


def _bullet_md(bullet: str) -> str:
    """Render 'Label: achievement' as a bold-label markdown bullet."""
    label, sep, rest = bullet.partition(": ")
    return f"- **{label}:** {rest}" if sep else f"- {bullet}"


def assemble_email(context: dict, row: PendingDraft, profile: SenderProfile) -> dict:
    """Fill the template from LLM context + sender profile → {subject, body, body_html}.

    Returns {} if the context is missing a required field (treated as terminal by
    the worker), so a half-populated email never reaches the mailbox.
    """
    if not all(context.get(k) for k in _REQUIRED):
        return {}

    bullets = "\n".join(_bullet_md(b) for b in context["tailored_bullets"])
    first_name = row.founder_name.split()[0] if row.founder_name else "there"
    values = {
        "first_name": first_name,
        "intro": profile.intro,
        "company_interest": context["company_interest"],
        "admiration_detail": context["admiration_detail"],
        "experience_bullets": bullets,
        "sender_first_name": profile.first_name,
        "github_link": f"[GitHub]({profile.github})",
        "linkedin_link": f"[LinkedIn]({profile.linkedin})",
    }
    filled = fill_template(TEMPLATE, values)
    return {
        "subject": context["subject"],
        "body": plain_text_fallback(filled),
        "body_html": markdown_to_html(filled),
    }
