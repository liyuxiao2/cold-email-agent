from cold_email.workers.research import research_task
import logging
from celery import shared_task
import httpx
from cold_email.config import settings

APOLLO_URL = "https://api.apollo.io/api/v1/mixed_people/search"


logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    name="cold_email.workers.discovery.discovery_task",
)
def discovery_task(self) -> dict:
    """
    Triggered by Celery Beat every Monday at 08:00.
    Steps (to be implemented):
      1. Query Apollo.io for early-stage fintech companies
      2. Find technical founder/CTO contact per company
      3. Deduplicate against existing leads by founder_email / company_url
      4. Insert new leads with status='found'
      5. Dispatch research_task.delay(lead_id) per new lead
    """ 

    #logic to populate new leads
    fetched_leads = fetch_apollo_candidates()  
    
    for lead in fetched_leads:
      # TODO: Persist lead to DB and use the local UUID
      research_task.delay(str(lead["id"]))

    return {"fetched": len(fetched_leads)}

def fetch_apollo_candidates() -> list[dict]:
    body = {
        "person_titles": settings.discovery_person_titles,                     # no []
        "person_seniorities": settings.discovery_person_seniorities,           # no []
        "person_locations": settings.discovery_person_locations,               # no []
        "organization_num_employees_ranges": [f"1,{settings.discovery_headcount_max}"],
        "organization_industries": ["fintech", "financial services"],
        "organization_latest_funding_stage_cd": ["seed", "series_a"],
        "contact_email_status": ["verified"],
        "include_similar_titles": False,                                        # real bool, not "false" string
        "per_page": settings.discovery_leads_per_run,
    }
  

    headers = {
        "Authorization": f"Bearer {settings.apollo_api_key}",
        "Content-Type": "application/json",
    }
    resp = httpx.post(APOLLO_URL, json=body, headers=headers, timeout=30.0)
    resp.raise_for_status()
    return resp.json()["people"]