import logging
from dataclasses import dataclass

from cold_email.database import Lead
from cold_email.workers.research.constants import RESEARCH
from cold_email.workers.research.helpers.db_helpers import fetch_lead
from cold_email.workers.research.helpers.extraction import find_company_url, is_probable_homepage
from cold_email.workers.shared.errors import handle_terminal_failure

logger = logging.getLogger(__name__)


@dataclass
class LeadResolution:
    lead: Lead | None = None
    url: str | None = None
    failure: dict | None = None


def resolve_lead_url(lead_id: str) -> LeadResolution:
    lead = fetch_lead(lead_id)

    if not lead:
        logger.error(f"Lead {lead_id} not found in DB")
        return LeadResolution(failure={"status": "failed", "error": "Lead not found"})

    # Trust the discovery-scraped company_url only if it actually looks like the
    # company's homepage; otherwise (aggregator/news link, or empty) fall back to
    # a slug-matched DDG search. This keeps a wrong domain from cascading into
    # scraping, LLM extraction, and the Hunter email lookup.
    if is_probable_homepage(lead.company_url, lead.company_name):
        company_url = lead.company_url
    else:
        company_url = find_company_url(lead)

    if not company_url:
        handle_terminal_failure(
            lead_id,
            f"Could not find company URL for {lead.company_name}",
            stage=RESEARCH,
            task_name="cold_email.workers.research.research_task",
        )
        return LeadResolution(
            lead=lead,
            failure={"status": "failed", "error": "Could not find company URL for lead"},
        )

    return LeadResolution(
        lead=lead,
        url=company_url,
    )
