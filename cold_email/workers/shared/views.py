"""Typed row shapes for the read-only pending_* database views.

One dataclass per view (see migration 006). Field names must match the view's
column aliases so a helper can build them with `Model(**row)`.

Both carry user_id: after the tenancy split, a worker must know whose profile
and mailbox to use.
"""

from dataclasses import dataclass


@dataclass
class PendingDraft:
    """One row of pending_drafts: a queued outreach row + company + contact + research."""

    outreach_id: str
    user_id: str
    company_id: str
    contact_id: str
    company_name: str
    company_url: str
    founder_name: str | None
    contact_email: str
    contact_first_name: str | None
    contact_position: str | None
    raw_content: str
    tech_stack: str | None
    recent_news: str | None
    hook: str | None


@dataclass
class PendingSend:
    """One row of pending_sends: an approved, due outreach row + its latest draft."""

    outreach_id: str
    user_id: str
    contact_email: str
    gmail_draft_id: str | None
    subject_line: str
    body: str
