from unittest.mock import MagicMock, patch

import pytest

from cold_email.workers.shared import llm


def _provider(generate_side_effect, fall_back):
    """A fake LLMProvider: generate() plays the given side effect, should_fall_back
    classifies via `fall_back`. Lets us test generate_json's orchestration without
    touching any real SDK."""
    p = MagicMock()
    p.generate.side_effect = generate_side_effect
    p.should_fall_back.side_effect = fall_back
    return p


def _run(provider, chain):
    """Run generate_json with `_provider_for` pinned to `provider` for every
    model, so the model names in `chain` just drive the loop."""
    with (
        patch.object(llm.settings, "model_fallback_chain", chain),
        patch.object(llm, "_provider_for", return_value=provider),
    ):
        return llm.generate_json(system="s", prompt="p", schema=object)


def test_falls_back_past_exhausted_model():
    """A fall-back-worthy error on the first model advances to the next; its
    result is returned."""
    p = _provider(
        [RuntimeError("429 RESOURCE_EXHAUSTED"), "OK"],
        lambda e: "RESOURCE_EXHAUSTED" in str(e),
    )
    result = _run(p, ["model-a", "model-b"])

    assert result == "OK"
    tried = [c.kwargs["model"] for c in p.generate.call_args_list]
    assert tried == ["model-a", "model-b"]


def test_reraises_non_quota_error_immediately():
    """An error should_fall_back rejects can't be fixed by swapping models, so
    it aborts the chain at the first model."""
    p = _provider([ValueError("bad request")], lambda e: False)
    with pytest.raises(ValueError):
        _run(p, ["model-a", "model-b"])

    assert p.generate.call_count == 1


def test_all_models_exhausted_reraises_last_error():
    """When every model is tapped out, re-raise so the caller's task retries."""
    p = _provider(
        [RuntimeError("429 RESOURCE_EXHAUSTED"), RuntimeError("429 RESOURCE_EXHAUSTED")],
        lambda e: True,
    )
    with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
        _run(p, ["model-a", "model-b"])

    assert p.generate.call_count == 2


def test_empty_chain_defaults_to_single_model():
    """An unset chain falls back to [settings.model_name] (legacy behavior)."""
    p = _provider(["OK"], lambda e: False)
    with (
        patch.object(llm.settings, "model_fallback_chain", []),
        patch.object(llm.settings, "model_name", "solo-model"),
        patch.object(llm, "_provider_for", return_value=p),
    ):
        result = llm.generate_json(system="s", prompt="p", schema=object)

    assert result == "OK"
    assert p.generate.call_args.kwargs["model"] == "solo-model"


def test_provider_routing():
    """Model name -> provider routing (the 'swap by name' mechanism)."""
    assert isinstance(llm._provider_for("gemini-3.5-flash-lite"), llm.GeminiProvider)
    assert isinstance(llm._provider_for("llama-3.3-70b-versatile"), llm.GroqProvider)
    with pytest.raises(ValueError):
        llm._provider_for("gpt-4o")
