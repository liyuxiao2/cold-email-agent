from unittest.mock import MagicMock, patch

import pytest

from cold_email.workers.shared import llm


def _client_with(side_effect):
    """A fake genai.Client whose models.generate_content behaves as given."""
    client = MagicMock()
    client.models.generate_content.side_effect = side_effect
    return client


def test_falls_back_past_exhausted_model():
    """A 429 on the first model advances to the next; its result is returned."""
    ok = MagicMock(name="response")
    calls = _client_with([Exception("429 RESOURCE_EXHAUSTED"), ok])
    with (
        patch.object(llm.settings, "model_fallback_chain", ["model-a", "model-b"]),
        patch.object(llm.genai, "Client", return_value=calls),
    ):
        result = llm.generate_with_fallback(contents="hi", config={})

    assert result is ok
    # Both models were tried, in order.
    tried = [c.kwargs["model"] for c in calls.models.generate_content.call_args_list]
    assert tried == ["model-a", "model-b"]


def test_falls_back_past_retired_model():
    """A 404 (model retired / not available) also advances to the next model."""
    ok = MagicMock(name="response")
    calls = _client_with([Exception("404 NOT_FOUND. model retired"), ok])
    with (
        patch.object(llm.settings, "model_fallback_chain", ["dead-model", "live-model"]),
        patch.object(llm.genai, "Client", return_value=calls),
    ):
        result = llm.generate_with_fallback(contents="hi", config={})

    assert result is ok
    assert calls.models.generate_content.call_count == 2


def test_reraises_non_quota_error_immediately():
    """A non-429 error can't be fixed by swapping models, so don't fall back."""
    calls = _client_with(ValueError("bad request"))
    with (
        patch.object(llm.settings, "model_fallback_chain", ["model-a", "model-b"]),
        patch.object(llm.genai, "Client", return_value=calls),
        pytest.raises(ValueError),
    ):
        llm.generate_with_fallback(contents="hi", config={})

    # Stopped at the first model — no fallback attempt.
    assert calls.models.generate_content.call_count == 1


def test_all_models_exhausted_reraises_last_quota_error():
    """When every model is tapped out, re-raise so the task retries later."""
    calls = _client_with(
        [Exception("429 RESOURCE_EXHAUSTED"), Exception("429 RESOURCE_EXHAUSTED")]
    )
    with (
        patch.object(llm.settings, "model_fallback_chain", ["model-a", "model-b"]),
        patch.object(llm.genai, "Client", return_value=calls),
        pytest.raises(Exception, match="RESOURCE_EXHAUSTED"),
    ):
        llm.generate_with_fallback(contents="hi", config={})

    assert calls.models.generate_content.call_count == 2


def test_empty_chain_defaults_to_single_model():
    """An unset chain falls back to [settings.model_name] (legacy behavior)."""
    ok = MagicMock(name="response")
    calls = _client_with([ok])
    with (
        patch.object(llm.settings, "model_fallback_chain", []),
        patch.object(llm.settings, "model_name", "solo-model"),
        patch.object(llm.genai, "Client", return_value=calls),
    ):
        result = llm.generate_with_fallback(contents="hi", config={})

    assert result is ok
    assert calls.models.generate_content.call_args.kwargs["model"] == "solo-model"
