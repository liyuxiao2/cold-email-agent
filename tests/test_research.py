from unittest.mock import MagicMock, patch

import pytest

from cold_email.database import Lead
from cold_email.workers.research.helpers.extraction import (
    call_gemini,
    find_company_url,
    is_probable_homepage,
    parse_gemini_response,
    scrape_website,
    select_best_url,
)
from cold_email.workers.research.helpers.preflight import resolve_lead_url
from cold_email.workers.research.research import research_task

FAKE_UUID = "00000000-0000-0000-0000-000000000000"


def test_is_probable_homepage():
    # Slug-matching, non-aggregator domain is the homepage.
    assert is_probable_homepage("https://acmecorp.com/about", "Acme Corp") is True
    # Aggregators/accelerators are rejected even if they'd slug-match otherwise.
    assert is_probable_homepage("https://techstars.com/acme", "Acme") is False
    assert is_probable_homepage("https://linkedin.com/company/acme", "Acme") is False
    # Domain that doesn't contain the company slug is rejected.
    assert is_probable_homepage("https://someothersite.com", "Acme Corp") is False
    assert is_probable_homepage(None, "Acme") is False


def test_resolve_lead_url_rejects_aggregator_company_url():
    """A discovery company_url pointing at an aggregator must NOT be trusted;
    resolve_lead_url falls back to the slug-matched DDG search instead."""
    lead = Lead(company_name="Acme", company_url="https://techstars.com/companies/acme")
    with (
        patch("cold_email.workers.research.helpers.preflight.fetch_lead", return_value=lead),
        patch(
            "cold_email.workers.research.helpers.preflight.find_company_url",
            return_value="https://acme.com",
        ) as find,
    ):
        resolution = resolve_lead_url(FAKE_UUID)

    # The aggregator company_url was rejected; the DDG fallback URL is used.
    assert resolution.url == "https://acme.com"
    find.assert_called_once()


def test_select_best_url():
    lead = Lead(company_name="Acme Corp")
    results = [
        {"url": "https://linkedin.com/company/acme"},
        {"url": "https://acmecorp.com/about"},
        {"url": "https://someothersite.com"},
    ]
    best_url = select_best_url(results, lead)
    # linkedin.com is in the aggregator blocklist, acmecorp.com slug-matches → wins
    assert best_url == "https://acmecorp.com/about"


def test_select_best_url_returns_none_when_no_domain_matches():
    """Regression: when no candidate domain slug-matches the company, return None
    instead of guessing the top result. In prod, guessing resolved accelerator /
    reference pages (techstars.com, wikipedia.org) as the 'homepage', which then
    failed the downstream Hunter email lookup at the wrong domain."""
    lead = Lead(company_name="Acme Corp")
    results = [
        {"url": "https://www.techstars.com/portfolio/acme"},
        {"url": "https://en.wikipedia.org/wiki/Acme_Corp"},
        {"url": "https://someunrelatedsite.com/acme"},
    ]
    assert select_best_url(results, lead) is None


def test_find_company_url():
    lead = Lead(company_name="Acme Corp", funding_stage="Seed")
    # DDG returns aggregators alongside the real site; select_best_url picks the
    # slug-matching homepage and skips the blocklisted LinkedIn result.
    ddg_results = [
        {"href": "https://linkedin.com/company/acme", "title": "Acme | LinkedIn"},
        {"href": "https://acmecorp.com/about", "title": "Acme Corp"},
    ]
    with patch("cold_email.workers.research.helpers.extraction.DDGS") as MockDDGS:
        MockDDGS.return_value.text.return_value = ddg_results
        url = find_company_url(lead)
        MockDDGS.return_value.text.assert_called_once()
        assert url == "https://acmecorp.com/about"


def test_find_company_url_propagates_search_errors():
    """A transient DDG failure (rate limit/network) must propagate so the task
    retries — not collapse to None and terminally fail the lead."""
    lead = Lead(company_name="Acme Corp", funding_stage="Seed")
    with patch("cold_email.workers.research.helpers.extraction.DDGS") as MockDDGS:
        MockDDGS.return_value.text.side_effect = RuntimeError("rate limited")
        with pytest.raises(RuntimeError):
            find_company_url(lead)


def test_call_gemini_uses_models_generate_content():
    """Regression guard for the google-genai API shape + provider routing.

    generate_content lives on client.models (the service), NOT on the Model
    object returned by client.models.get(). With a Gemini model in the chain,
    call_gemini must route through GeminiProvider and return the raw JSON text.
    """
    fake_response = MagicMock()
    fake_response.text = (
        '{"tech_stack": ["Python"], "recent_news": "Raised seed", "hook": "Ledger infra"}'
    )
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = fake_response

    # Pin the chain to a Gemini model so _provider_for routes to GeminiProvider,
    # and patch the client the provider constructs.
    with patch("cold_email.workers.shared.llm.settings.model_fallback_chain",
               ["gemini-3.5-flash-lite"]), \
         patch("cold_email.workers.shared.llm.genai.Client", return_value=mock_client):
        raw = call_gemini("scraped text", "Acme Corp")

    mock_client.models.generate_content.assert_called_once()
    mock_client.models.get.assert_not_called()
    # Structured-output config, not the old Anthropic-shaped tools param.
    _, kwargs = mock_client.models.generate_content.call_args
    assert kwargs["model"] == "gemini-3.5-flash-lite"
    assert kwargs["config"]["response_mime_type"] == "application/json"
    assert "tools" not in kwargs["config"]

    parsed = parse_gemini_response(raw)
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


def test_research_task_lead_not_found():
    # If lead is not found, research_task should return status: failed
    with patch("cold_email.workers.research.helpers.preflight.fetch_lead", return_value=None):
        result = research_task.apply(args=[FAKE_UUID]).get(propagate=True)
        assert result == {"status": "failed", "error": "Lead not found"}


def test_research_task_persists_raw_llm_text():
    """Regression: call_gemini returns a plain JSON string (not an object with
    .text), so the task must persist that string as raw_content. The old code
    did `response.text` and crashed with AttributeError in prod."""
    resolution = MagicMock(failure=None, url="https://acme.com")
    resolution.lead = Lead(company_name="Acme")
    raw = '{"tech_stack": ["Python"], "recent_news": "Seed", "hook": "Ledger"}'

    module = "cold_email.workers.research.research"
    with (
        patch(f"{module}.resolve_lead_url", return_value=resolution),
        patch(f"{module}.scrape_website", return_value="scraped"),
        patch(f"{module}.call_gemini", return_value=raw),
        patch(f"{module}.commit_research") as commit,
        patch(f"{module}.find_email", return_value={"email": "ada@acme.com", "score": 90}),
        patch(f"{module}.save_founder_contact") as save_contact,
        patch(f"{module}.update_lead_status"),
    ):
        result = research_task.apply(args=[FAKE_UUID]).get(propagate=True)

    assert result == {"status": "success"}
    assert commit.call_args.kwargs["raw_content"] == raw
    assert commit.call_args.kwargs["tech_stack"] == ["Python"]
    # An accepted email is persisted on the lead.
    assert save_contact.call_args.args[2] == "ada@acme.com"


def test_research_task_fails_fast_when_no_email():
    """No usable email -> the lead is dead-lettered at the research stage and
    never advances to 'researched' (so it doesn't waste the drafting stage)."""
    resolution = MagicMock(failure=None, url="https://acme.com")
    resolution.lead = Lead(company_name="Acme")
    raw = '{"founder_name": "Ada", "tech_stack": [], "recent_news": "", "hook": "h"}'

    module = "cold_email.workers.research.research"
    with (
        patch(f"{module}.resolve_lead_url", return_value=resolution),
        patch(f"{module}.scrape_website", return_value="scraped"),
        patch(f"{module}.call_gemini", return_value=raw),
        patch(f"{module}.commit_research"),
        patch(f"{module}.find_email", return_value=None),  # Hunter found nothing
        patch(f"{module}.handle_terminal_failure") as terminal,
        patch(f"{module}.update_lead_status") as update_status,
    ):
        result = research_task.apply(args=[FAKE_UUID]).get(propagate=True)

    assert result["status"] == "failed"
    # Dead-lettered at the research stage...
    assert terminal.call_args.kwargs["stage"] == "research"
    # ...and never marked 'researched'.
    update_status.assert_not_called()
