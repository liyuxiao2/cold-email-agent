from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from cold_email.workers.shared import llm


class _Schema(BaseModel):
    ok: bool


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
    model, so the model names in `chain` just drive the loop.

    Also pins `acquire` to always grant a token: these tests exercise the
    fallback/retry orchestration, not the token bucket, and the unit suite must
    stay runnable with no Redis — see test_rate_limit.py's `integration` mark.
    """
    with (
        patch.object(llm.settings, "model_fallback_chain", chain),
        patch.object(llm, "_provider_for", return_value=provider),
        patch.object(llm, "acquire", return_value=True),
    ):
        return llm.generate_json(system="s", prompt="p", schema=object)


def _stub_one_model_success(monkeypatch):
    """Patch `_provider_for` so the (single) model in the chain succeeds
    immediately, returning a fixed JSON payload."""
    provider = MagicMock()
    provider.generate.return_value = '{"ok": true}'
    monkeypatch.setattr(llm, "_provider_for", lambda model: provider)
    monkeypatch.setattr(llm.settings, "model_fallback_chain", ["llama-3.3-70b-versatile"])
    return provider


def _stub_chain(monkeypatch, calls, chain):
    """Patch `_provider_for` so every model in `chain` succeeds, recording the
    model name it was invoked with into `calls`."""

    def _provider_for(model):
        provider = MagicMock()
        provider.generate.side_effect = lambda **kwargs: (
            calls.append(kwargs["model"]),
            '{"ok": true}',
        )[1]
        return provider

    monkeypatch.setattr(llm, "_provider_for", _provider_for)
    monkeypatch.setattr(llm.settings, "model_fallback_chain", chain)


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
        patch.object(llm, "acquire", return_value=True),
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


@pytest.mark.asyncio
async def test_resolve_returns_platform_credentials_without_a_user_key(
    async_session, admin_user_id
):
    from cold_email.database import User
    from cold_email.workers.shared.llm import resolve_llm_credentials

    creds = resolve_llm_credentials(await async_session.get(User, admin_user_id))
    assert creds.is_byok is False


@pytest.mark.asyncio
async def test_resolve_decrypts_a_user_key(async_session, admin_user_id):
    from cold_email.auth.crypto import encrypt
    from cold_email.database import User
    from cold_email.workers.shared.llm import resolve_llm_credentials

    user = await async_session.get(User, admin_user_id)
    user.llm_api_key_enc = encrypt("gsk_userkey")
    user.llm_provider = "groq"
    await async_session.commit()

    creds = resolve_llm_credentials(user)
    assert creds.api_key == "gsk_userkey"
    assert creds.provider == "groq"
    assert creds.is_byok is True


def test_platform_calls_acquire_a_token(monkeypatch):
    acquired = []
    monkeypatch.setattr(
        "cold_email.workers.shared.llm.acquire",
        lambda key, **kw: acquired.append(key) or True,
    )
    _stub_one_model_success(monkeypatch)

    from cold_email.workers.shared.llm import LlmCredentials, generate_json

    generate_json(
        system="s",
        prompt="p",
        schema=_Schema,
        credentials=LlmCredentials(api_key="platform", provider=None, is_byok=False),
    )
    assert len(acquired) == 1


def test_byok_calls_bypass_the_bucket(monkeypatch):
    """Their key, their limits — the shared bucket models OUR quota."""
    acquired = []
    monkeypatch.setattr(
        "cold_email.workers.shared.llm.acquire",
        lambda key, **kw: acquired.append(key) or True,
    )
    _stub_one_model_success(monkeypatch)

    from cold_email.workers.shared.llm import LlmCredentials, generate_json

    generate_json(
        system="s",
        prompt="p",
        schema=_Schema,
        credentials=LlmCredentials(api_key="gsk_theirs", provider="groq", is_byok=True),
    )
    assert acquired == []


def test_a_bucket_timeout_skips_to_the_next_model(monkeypatch):
    """Treated exactly like a 429, reusing the existing skip logic rather than
    adding a second notion of 'this model is unavailable'."""
    calls = []

    monkeypatch.setattr(
        "cold_email.workers.shared.llm.acquire",
        # First model's bucket is empty, second has room.
        lambda key, **kw: "flash-lite" in key,
    )
    _stub_chain(monkeypatch, calls, chain=["llama-3.3-70b-versatile", "gemini-3.5-flash-lite"])

    from cold_email.workers.shared.llm import generate_json

    generate_json(system="s", prompt="p", schema=_Schema)
    assert calls == ["gemini-3.5-flash-lite"]


def test_min_interval_sleep_constant_is_gone():
    """time.sleep paced one worker. The bucket paces the fleet."""
    import cold_email.workers.shared.constants as c

    assert not hasattr(c, "LLM_MIN_INTERVAL_SECONDS")
