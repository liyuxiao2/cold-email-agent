import logging
from dataclasses import dataclass

from cold_email.database import Company
from cold_email.workers.research.constants import RESEARCH
from cold_email.workers.research.helpers.db_helpers import fetch_company
from cold_email.workers.research.helpers.extraction import find_company_url, is_probable_homepage
from cold_email.workers.shared.errors import fail_company

logger = logging.getLogger(__name__)


@dataclass
class CompanyResolution:
    company: Company | None = None
    url: str | None = None
    failure: dict | None = None


def resolve_company_url(company_id: str) -> CompanyResolution:
    company = fetch_company(company_id)

    if not company:
        logger.error(f"Company {company_id} not found in DB")
        return CompanyResolution(failure={"status": "failed", "error": "Company not found"})

    # Trust the discovery-scraped company_url only if it actually looks like the
    # company's homepage; otherwise (aggregator/news link, or empty) fall back to
    # a slug-matched DDG search. This keeps a wrong domain from cascading into
    # scraping, LLM extraction, and the Hunter contact lookup.
    if is_probable_homepage(company.company_url, company.company_name):
        company_url = company.company_url
    else:
        company_url = find_company_url(company)

    if not company_url:
        fail_company(
            company_id,
            f"Could not find company URL for {company.company_name}",
            stage=RESEARCH,
            task_name="cold_email.workers.research.research_task",
        )
        return CompanyResolution(
            company=company,
            failure={"status": "failed", "error": "Could not find company URL for company"},
        )

    return CompanyResolution(
        company=company,
        url=company_url,
    )
