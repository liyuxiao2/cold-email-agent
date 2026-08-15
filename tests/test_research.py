import json
from unittest.mock import MagicMock, patch

import pytest

from cold_email.database import Company
from cold_email.workers.research.helpers.extraction import (
    call_llm_extraction,
    find_company_url,
    is_probable_homepage,
    parse_llm_response,
    scrape_website,
    select_best_url,
)
from cold_email.workers.research.helpers.preflight import resolve_company_url
from cold_email.workers.research.research import research_task

FAKE_UUID = "00000000-0000-0000-0000-000000000000"


def _stub_scrape_and_llm(monkeypatch, founder_name: str):
    """Stub the scrape + LLM extraction steps so tests exercise only the
    contact-finding path."""
    monkeypatch.setattr(
        "cold_email.workers.research.research.scrape_website", lambda url: "page text"
    )
    monkeypatch.setattr(
        "cold_email.workers.research.research.call_llm_extraction",
        lambda text, name: json.dumps(
            {
                "founder_name": founder_name,
                "tech_stack": ["python"],
                "recent_news": "news",
                "hook": "hook",
            }
        ),
    )


def test_is_probable_homepage():
    # Slug-matching, non-aggregator domain is the homepage.
    assert is_probable_homepage("https://acmecorp.com/about", "Acme Corp") is True
    # Aggregators/accelerators are rejected even if they'd slug-match otherwise.
    assert is_probable_homepage("https://techstars.com/acme", "Acme") is False
    assert is_probable_homepage("https://linkedin.com/company/acme", "Acme") is False
    # Domain that doesn't contain the company slug is rejected.
    assert is_probable_homepage("https://someothersite.com", "Acme Corp") is False
    assert is_probable_homepage(None, "Acme") is False


def test_resolve_company_url_rejects_aggregator_company_url():
    """A discovery company_url pointing at an aggregator must NOT be trusted;
    resolve_company_url falls back to the slug-matched DDG search instead."""
    company = Company(company_name="Acme", company_url="https://techstars.com/companies/acme")
    with (
        patch("cold_email.workers.research.helpers.preflight.fetch_company", return_value=company),
        patch(
            "cold_email.workers.research.helpers.preflight.find_company_url",
            return_value="https://acme.com",
        ) as find,
    ):
        resolution = resolve_company_url(FAKE_UUID)

    # The aggregator company_url was rejected; the DDG fallback URL is used.
    assert resolution.url == "https://acme.com"
    find.assert_called_once()


def test_resolve_company_url_not_found():
    with patch("cold_email.workers.research.helpers.preflight.fetch_company", return_value=None):
        resolution = resolve_company_url(FAKE_UUID)
    assert resolution.failure == {"status": "failed", "error": "Company not found"}


def test_select_best_url():
    company = Company(company_name="Acme Corp")
    results = [
        {"url": "https://linkedin.com/company/acme"},
        {"url": "https://acmecorp.com/about"},
        {"url": "https://someothersite.com"},
    ]
    best_url = select_best_url(results, company)
    # linkedin.com is in the aggregator blocklist, acmecorp.com slug-matches → wins
    assert best_url == "https://acmecorp.com/about"


def test_select_best_url_returns_none_when_no_domain_matches():
    """Regression: when no candidate domain slug-matches the company, return None
    instead of guessing the top result. In prod, guessing resolved accelerator /
    reference pages (techstars.com, wikipedia.org) as the 'homepage', which then
    failed the downstream Hunter contact lookup at the wrong domain."""
    company = Company(company_name="Acme Corp")
    results = [
        {"url": "https://www.techstars.com/portfolio/acme"},
        {"url": "https://en.wikipedia.org/wiki/Acme_Corp"},
        {"url": "https://someunrelatedsite.com/acme"},
    ]
    assert select_best_url(results, company) is None


def test_find_company_url():
    company = Company(company_name="Acme Corp", funding_stage="Seed")
    # DDG returns aggregators alongside the real site; select_best_url picks the
    # slug-matching homepage and skips the blocklisted LinkedIn result.
    ddg_results = [
        {"href": "https://linkedin.com/company/acme", "title": "Acme | LinkedIn"},
        {"href": "https://acmecorp.com/about", "title": "Acme Corp"},
    ]
    with patch("cold_email.workers.research.helpers.extraction.DDGS") as MockDDGS:
        MockDDGS.return_value.text.return_value = ddg_results
        url = find_company_url(company)
        MockDDGS.return_value.text.assert_called_once()
        assert url == "https://acmecorp.com/about"


def test_find_company_url_propagates_search_errors():
    """A transient DDG failure (rate limit/network) must propagate so the task
    retries — not collapse to None and terminally fail the company."""
    company = Company(company_name="Acme Corp", funding_stage="Seed")
    with patch("cold_email.workers.research.helpers.extraction.DDGS") as MockDDGS:
        MockDDGS.return_value.text.side_effect = RuntimeError("rate limited")
        with pytest.raises(RuntimeError, match="rate limited"):
            find_company_url(company)


def test_call_llm_extraction_uses_models_generate_content():
    """Regression guard for the google-genai API shape + provider routing.

    generate_content lives on client.models (the service), NOT on the Model
    object returned by client.models.get(). With a Gemini model in the chain,
    call_llm_extraction must route through GeminiProvider and return the raw JSON text.
    """
    fake_response = MagicMock()
    fake_response.text = (
        '{"tech_stack": ["Python"], "recent_news": "Raised seed", "hook": "Ledger infra"}'
    )
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = fake_response

    # Pin the chain to a Gemini model so _provider_for routes to GeminiProvider,
    # and patch the client the provider constructs.
    with (
        patch(
            "cold_email.workers.shared.llm.settings.model_fallback_chain", ["gemini-3.5-flash-lite"]
        ),
        patch("cold_email.workers.shared.llm.genai.Client", return_value=mock_client),
    ):
        raw = call_llm_extraction("scraped text", "Acme Corp")

    mock_client.models.generate_content.assert_called_once()
    mock_client.models.get.assert_not_called()
    # Structured-output config, not the old Anthropic-shaped tools param.
    _, kwargs = mock_client.models.generate_content.call_args
    assert kwargs["model"] == "gemini-3.5-flash-lite"
    assert kwargs["config"]["response_mime_type"] == "application/json"
    assert "tools" not in kwargs["config"]

    parsed = parse_llm_response(raw)
    assert parsed["tech_stack"] == ["Python"]
    assert parsed["hook"] == "Ledger infra"


def test_scrape_website_soup():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = (
        b"<html><body><script>console.log('test')</script><p>"
        + b"Hello World " * 30
        + b"</p></body></html>"
    )
    with patch("requests.get", return_value=mock_response) as mock_get:
        text = scrape_website("https://example.com")
        mock_get.assert_called_once()
        # Script tags are stripped; plain text content is extracted
        assert "Hello World" in text
        assert "console.log" not in text


def test_scrape_website_firecrawl_fallback():
    mock_response = MagicMock()
    # Short content triggers Firecrawl fallback
    mock_response.status_code = 200
    mock_response.content = b"<html><body><p>Hi</p></body></html>"

    mock_fc_response = MagicMock()
    mock_fc_response.markdown = "Hello from Firecrawl fallback!"

    with (
        patch("requests.get", return_value=mock_response),
        patch("cold_email.workers.research.helpers.extraction.FirecrawlApp") as MockFC,
    ):
        MockFC.return_value.scrape.return_value = mock_fc_response
        text = scrape_website("https://example.com")
        assert text == "Hello from Firecrawl fallback!"


def test_research_task_company_not_found():
    with patch("cold_email.workers.research.helpers.preflight.fetch_company", return_value=None):
        result = research_task(FAKE_UUID)
        assert result == {"status": "failed", "error": "Company not found"}


def test_research_task_persists_raw_llm_text():
    """Regression: call_llm_extraction returns a plain JSON string (not an object with
    .text), so the task must persist that string as raw_content. The old code
    did `response.text` and crashed with AttributeError in prod."""
    resolution = MagicMock(failure=None, url="https://acme.com")
    resolution.company = Company(company_name="Acme")
    raw = '{"tech_stack": ["Python"], "recent_news": "Seed", "hook": "Ledger"}'

    module = "cold_email.workers.research.research"
    with (
        patch(f"{module}.resolve_company_url", return_value=resolution),
        patch(f"{module}.scrape_website", return_value="scraped"),
        patch(f"{module}.call_llm_extraction", return_value=raw),
        patch(f"{module}.commit_research") as commit,
        patch(f"{module}.find_contacts", return_value=[]),
        patch(f"{module}.classify_contacts", return_value=["a-contact"]),
        patch(f"{module}.save_contacts") as save_contacts_mock,
        patch(f"{module}.has_eligible_contact", return_value=True),
        patch(f"{module}.update_company_research_status") as update_status,
    ):
        result = research_task(FAKE_UUID)

    assert result == {"status": "success", "contacts": 1}
    assert commit.call_args.kwargs["raw_content"] == raw
    assert commit.call_args.kwargs["tech_stack"] == ["Python"]
    # Every contact is persisted, regardless of eligibility.
    save_contacts_mock.assert_called_once()
    update_status.assert_called_once()


def test_research_task_fails_fast_when_no_eligible_contacts():
    """No eligible contact -> the company is dead-lettered at the research stage
    and never advances to 'researched' (so it doesn't waste the drafting stage)."""
    resolution = MagicMock(failure=None, url="https://acme.com")
    resolution.company = Company(company_name="Acme")
    raw = '{"founder_name": "Ada", "tech_stack": [], "recent_news": "", "hook": "h"}'

    module = "cold_email.workers.research.research"
    with (
        patch(f"{module}.resolve_company_url", return_value=resolution),
        patch(f"{module}.scrape_website", return_value="scraped"),
        patch(f"{module}.call_llm_extraction", return_value=raw),
        patch(f"{module}.commit_research"),
        patch(f"{module}.find_contacts", return_value=[]),
        patch(f"{module}.classify_contacts", return_value=[]),
        patch(f"{module}.save_contacts") as save_contacts_mock,
        patch(f"{module}.has_eligible_contact", return_value=False),
        patch(f"{module}.fail_company") as fail_mock,
        patch(f"{module}.update_company_research_status") as update_status,
    ):
        result = research_task(FAKE_UUID)

    assert result["status"] == "failed"
    # Contacts are saved before the eligibility gate...
    save_contacts_mock.assert_called_once()
    # ...dead-lettered at the research stage...
    assert fail_mock.call_args.kwargs["stage"] == "research"
    # ...and never marked 'researched'.
    update_status.assert_not_called()


@pytest.mark.asyncio
async def test_saves_every_contact_including_ineligible(
    async_session, monkeypatch, sync_session_for
):
    """Ineligible contacts are stored so a future loosening of the position
    filter can re-classify them without re-spending Hunter credits."""
    from cold_email.database import CompanyContact
    from cold_email.workers.research.helpers.contact_finder import HunterContact

    company = Company(company_name="Acme", company_url="https://acme.com")
    async_session.add(company)
    await async_session.commit()

    monkeypatch.setattr(
        "cold_email.workers.research.research.find_contacts",
        lambda domain: [
            HunterContact("cto@acme.com", "Ann", "Reed", "CTO", "executive", "it", 90, False),
            HunterContact("info@acme.com", None, None, None, None, None, 80, True),
        ],
    )
    _stub_scrape_and_llm(monkeypatch, founder_name="Ann Reed")

    research_task(str(company.id))

    from sqlalchemy import select

    contacts = (
        (
            await async_session.execute(
                select(CompanyContact).where(CompanyContact.company_id == company.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(contacts) == 2
    assert {c.eligible for c in contacts} == {True, False}


@pytest.mark.asyncio
async def test_no_eligible_contact_fails_the_company(async_session, monkeypatch, sync_session_for):
    from cold_email.database import RESEARCH_FAILED, DeadLetter
    from cold_email.workers.research.constants import ERR_NO_ELIGIBLE_CONTACTS
    from cold_email.workers.research.helpers.contact_finder import HunterContact

    company = Company(company_name="Acme", company_url="https://acme.com")
    async_session.add(company)
    await async_session.commit()

    monkeypatch.setattr(
        "cold_email.workers.research.research.find_contacts",
        lambda domain: [HunterContact("info@acme.com", None, None, None, None, None, 80, True)],
    )
    _stub_scrape_and_llm(monkeypatch, founder_name="Ann Reed")

    result = research_task(str(company.id))
    assert result["status"] == "failed"

    await async_session.refresh(company)
    assert company.research_status == RESEARCH_FAILED

    from sqlalchemy import select

    dl = (await async_session.execute(select(DeadLetter))).scalar_one()
    assert dl.company_id == company.id
    assert dl.outreach_id is None
    assert dl.error_msg == ERR_NO_ELIGIBLE_CONTACTS


@pytest.mark.asyncio
async def test_contacts_are_saved_before_the_eligibility_gate(
    async_session, monkeypatch, sync_session_for
):
    """Even a company that fails research keeps its contact rows."""
    from cold_email.database import CompanyContact
    from cold_email.workers.research.helpers.contact_finder import HunterContact

    company = Company(company_name="Acme", company_url="https://acme.com")
    async_session.add(company)
    await async_session.commit()

    monkeypatch.setattr(
        "cold_email.workers.research.research.find_contacts",
        lambda domain: [HunterContact("info@acme.com", None, None, None, None, None, 80, True)],
    )
    _stub_scrape_and_llm(monkeypatch, founder_name="Ann Reed")

    research_task(str(company.id))

    from sqlalchemy import func, select

    count = (
        await async_session.execute(
            select(func.count())
            .select_from(CompanyContact)
            .where(CompanyContact.company_id == company.id)
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_save_contacts_is_idempotent(async_session, sync_session_for):
    """A retried research task must not duplicate contacts.
    UNIQUE(company_id, email) + ON CONFLICT DO NOTHING."""
    from cold_email.database import CompanyContact
    from cold_email.workers.research.helpers.contact_finder import (
        ClassifiedContact,
        HunterContact,
    )
    from cold_email.workers.research.helpers.db_helpers import save_contacts

    company = Company(company_name="Acme")
    async_session.add(company)
    await async_session.commit()

    contact = ClassifiedContact(
        contact=HunterContact("a@acme.com", "A", "B", "CTO", None, None, 90, False),
        is_founder=False,
        eligible=True,
    )
    save_contacts(str(company.id), [contact])
    save_contacts(str(company.id), [contact])

    from sqlalchemy import func, select

    assert (
        await async_session.execute(select(func.count()).select_from(CompanyContact))
    ).scalar_one() == 1
