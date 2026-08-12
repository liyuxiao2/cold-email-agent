from unittest.mock import MagicMock, patch

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
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"web": {"results": [{"url": "https://acme.com"}]}}
    with patch(
        "cold_email.workers.research.helpers.extraction.httpx.get", return_value=mock_response
    ) as mock_get:
        url = find_company_url(lead)
        mock_get.assert_called_once()
        assert url == "https://acme.com"


def test_find_company_url_raises_on_api_error():
    """A non-200 from Brave (e.g. 402 quota, 429 rate limit) must raise so the
    task retries — not collapse to None and terminally fail the lead."""
    import httpx

    lead = Lead(company_name="Acme Corp", funding_stage="Seed")
    mock_response = MagicMock()
    mock_response.status_code = 402
    mock_response.text = '{"type":"ErrorResponse","error":"quota exceeded"}'
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "402", request=MagicMock(), response=mock_response
    )
    with patch(
        "cold_email.workers.research.helpers.extraction.httpx.get", return_value=mock_response
    ):
        try:
            find_company_url(lead)
            assert False, "expected find_company_url to raise on HTTP 402"
        except httpx.HTTPStatusError:
            pass
        # It must NOT reach the json()/parse path on a non-200.
        mock_response.json.assert_not_called()


def test_call_gemini_uses_models_generate_content():
    """Regression guard for the google-genai API shape.

    generate_content lives on client.models (the service), NOT on the Model
    object returned by client.models.get(). The old code called
    client.models.get(...).generate_content(...) and crashed with
    AttributeError once it was finally exercised in prod.
    """
    fake_response = MagicMock()
    fake_response.text = (
        '{"tech_stack": ["Python"], "recent_news": "Raised seed", "hook": "Ledger infra"}'
    )
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = fake_response

    with patch(
        "cold_email.workers.research.helpers.extraction.genai.Client",
        return_value=mock_client,
    ):
        response = call_gemini("scraped text", "Acme Corp")

    mock_client.models.generate_content.assert_called_once()
    mock_client.models.get.assert_not_called()
    # Structured-output config, not the old Anthropic-shaped tools param.
    _, kwargs = mock_client.models.generate_content.call_args
    assert kwargs["model"]
    assert kwargs["config"]["response_mime_type"] == "application/json"
    assert "tools" not in kwargs["config"]

    parsed = parse_gemini_response(response)
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
