"""Typed row shapes for the read-only pending_* database views.

One dataclass per view (see migrations/002 and 003). Field names must match the
view's column aliases so a helper can build them with `Model(**row)`. Both the
drafting and logistics workers read from a view, so these DTOs live at the
shared level rather than inside either worker.
"""

from dataclasses import dataclass


@dataclass
class PendingDraft:
    """One row of the pending_drafts view: a researched lead + its latest research."""

    lead_id: str
    company_name: str
    founder_name: str
    founder_email: str
    company_url: str
    raw_content: str
    tech_stack: str | None
    recent_news: str | None
    hook: str | None


@dataclass
class PendingSend:
    """One row of the pending_sends view: an approved lead + its latest draft."""

    lead_id: str
    founder_email: str
    gmail_draft_id: str | None
    subject_line: str
    body: str
