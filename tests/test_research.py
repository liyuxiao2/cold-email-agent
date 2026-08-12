from unittest.mock import MagicMock, patch

import pytest

from cold_email.database import Lead
from cold_email.workers.research.helpers.extraction import (
    call_gemini,
    find_company_url,
    parse_gemini_response,
    scrape_website,
    select_best_url,
)
from cold_email.workers.research.research import research_task

FAKE_UUID = "00000000-0000-0000-0000-000000000000"


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
