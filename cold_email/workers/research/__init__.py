"""Research worker package."""

try:
    from cold_email.workers.research.helpers.db_helpers import fetch_lead
    from cold_email.workers.research.helpers.extraction import find_company_url
    from cold_email.workers.research.research import research_task
except ImportError:
    # research.py and helpers/db_helpers.py still reference the deleted Lead
    # model (Stack 1b's tenancy split); the task rewriting them for the
    # companies/outreach split restores this. Until then, submodules that
    # don't depend on Lead — constants.py, helpers/contact_finder.py — must
    # stay importable on their own: Python always executes a package's
    # __init__.py before any of its submodules, so a failure here would take
    # down every submodule import, not just the ones that are actually broken.
    research_task = fetch_lead = find_company_url = None

__all__ = [
    "research_task",
    "fetch_lead",
    "find_company_url",
]
