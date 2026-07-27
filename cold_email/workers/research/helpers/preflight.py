import logging
from dataclasses import dataclass

from cold_email.database import Lead
from cold_email.workers.research.helpers.db_helpers import (
    fetch_lead,
    update_lead_status,
)
from cold_email.workers.research.helpers.extraction import find_company_url

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
        return LeadResolution(
            failure={"status": "failed", "error": "Lead not found"}
        )

    if lead.company_url:
        company_url = lead.company_url
    else:
        company_url = find_company_url(lead)

    if not company_url:
        logger.error(f"Could not find company URL for lead {lead_id}")
        update_lead_status(
            lead_id,
            status="failed",
            error_msg=f"Could not find company URL for {lead.company_name}",
        )
        return LeadResolution(
            lead=lead,
            failure={"status": "failed", "error": "Could not find company URL for lead"},
        )

    return LeadResolution(
        lead=lead,
        url=company_url,
    )
