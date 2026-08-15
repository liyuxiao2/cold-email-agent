from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from cold_email.workers.shared import llm


class _Schema(BaseModel):
    ok: bool


def _provider(generate_side_effect, fall_back, auth_error=False):
    """A fake LLMProvider: generate() plays the given side effect, should_fall_back
    classifies via `fall_back`. Lets us test generate_json's orchestration without
    touching any real SDK.

    is_auth_error defaults to False (not a MagicMock auto-attribute, which is
    truthy) so these tests exercise the fallback path, not the auth-error path,
    unless a test explicitly opts in via `auth_error=True`.
    """
    p = MagicMock()
    p.generate.side_effect = generate_side_effect
    p.should_fall_back.side_effect = fall_back
    p.is_auth_error.return_value = auth_error
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
    provider.is_auth_error.return_value = False
    monkeypatch.setattr(llm, "_provider_for", lambda model: provider)
    monkeypatch.setattr(llm.settings, "model_fallback_chain", ["llama-3.3-70b-versatile"])
    return provider


def _stub_chain(monkeypatch, calls, chain):
    """Patch `_provider_for` so every model in `chain` succeeds, recording the
    model name it was invoked with into `calls`."""

    def _provider_for(model):
        provider = MagicMock()
        provider.is_auth_error.return_value = False
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


def test_byok_gemini_key_reaches_gemini_never_groq(monkeypatch):
    """A saved Gemini key must never be handed to Groq, even though Groq's
    model is earlier in the default chain — Groq's AuthenticationError isn't
    fall-back-worthy, so generate_json would otherwise raise immediately and
    never reach the model the key actually works for."""
    from cold_email.workers.shared.llm import LlmCredentials, generate_json

    groq_calls = []
    gemini_calls = []
    monkeypatch.setattr(
        llm._GROQ, "generate", lambda **kw: groq_calls.append(kw) or "SHOULD NOT BE CALLED"
    )
    monkeypatch.setattr(
        llm._GEMINI, "generate", lambda **kw: gemini_calls.append(kw) or '{"ok": true}'
    )
    monkeypatch.setattr(
        llm.settings,
        "model_fallback_chain",
        ["llama-3.3-70b-versatile", "gemini-3.5-flash-lite"],
    )

    result = generate_json(
        system="s",
        prompt="p",
        schema=_Schema,
        credentials=LlmCredentials(api_key="AIza-theirs", provider="gemini", is_byok=True),
    )

    assert result == '{"ok": true}'
    assert groq_calls == []
    assert gemini_calls[0]["api_key"] == "AIza-theirs"


def test_byok_groq_key_reaches_groq(monkeypatch):
    """The mirror of the Gemini case: a saved Groq key must reach Groq."""
    from cold_email.workers.shared.llm import LlmCredentials, generate_json

    groq_calls = []
    gemini_calls = []
    monkeypatch.setattr(llm._GROQ, "generate", lambda **kw: groq_calls.append(kw) or '{"ok": true}')
    monkeypatch.setattr(
        llm._GEMINI, "generate", lambda **kw: gemini_calls.append(kw) or "SHOULD NOT BE CALLED"
    )
    monkeypatch.setattr(
        llm.settings,
        "model_fallback_chain",
        ["llama-3.3-70b-versatile", "gemini-3.5-flash-lite"],
    )

    result = generate_json(
        system="s",
        prompt="p",
        schema=_Schema,
        credentials=LlmCredentials(api_key="gsk_theirs", provider="groq", is_byok=True),
    )

    assert result == '{"ok": true}'
    assert gemini_calls == []
    assert groq_calls[0]["api_key"] == "gsk_theirs"


def test_byok_provider_missing_from_chain_raises_clearly(monkeypatch):
    """A provider with no model anywhere in the configured chain must fail
    loudly and by name, not silently fall through to the platform key."""
    from cold_email.workers.shared.llm import LlmCredentials, generate_json

    monkeypatch.setattr(llm.settings, "model_fallback_chain", ["gemini-3.5-flash-lite"])

    with pytest.raises(ValueError, match="groq"):
        generate_json(
            system="s",
            prompt="p",
            schema=_Schema,
            credentials=LlmCredentials(api_key="gsk_theirs", provider="groq", is_byok=True),
        )


def test_non_byok_call_walks_the_full_chain_unchanged():
    """A platform-key call must never be restricted by provider — even a
    stray `provider` value on a non-BYOK credentials object (shouldn't
    happen, but must not matter) leaves the whole chain intact."""
    from cold_email.workers.shared.llm import LlmCredentials

    p = _provider(
        [RuntimeError("429 RESOURCE_EXHAUSTED"), "OK"],
        lambda e: "RESOURCE_EXHAUSTED" in str(e),
    )
    with (
        patch.object(llm.settings, "model_fallback_chain", ["model-a", "model-b"]),
        patch.object(llm, "_provider_for", return_value=p),
        patch.object(llm, "acquire", return_value=True),
    ):
        result = llm.generate_json(
            system="s",
            prompt="p",
            schema=_Schema,
            credentials=LlmCredentials(api_key=None, provider="gemini", is_byok=False),
        )

    assert result == "OK"
    tried = [c.kwargs["model"] for c in p.generate.call_args_list]
    assert tried == ["model-a", "model-b"]


def test_auth_error_is_terminal_not_fall_back_worthy():
    """A wrong key will never succeed on retry: it must raise
    LlmAuthenticationError immediately rather than falling back to the next
    model (even one that WOULD otherwise be tried) or looping the chain."""
    from cold_email.workers.shared.llm import LlmAuthenticationError, LlmCredentials

    p = _provider(
        [RuntimeError("401 invalid api key")],
        fall_back=lambda e: True,  # would fall back if is_auth_error didn't win first
        auth_error=True,
    )
    with (
        patch.object(
            llm.settings,
            "model_fallback_chain",
            ["llama-3.3-70b-versatile", "gemini-3.5-flash-lite"],
        ),
        patch.object(llm, "_provider_for", return_value=p),
        patch.object(llm, "acquire", return_value=True),
        pytest.raises(LlmAuthenticationError),
    ):
        llm.generate_json(
            system="s",
            prompt="p",
            schema=_Schema,
            credentials=LlmCredentials(api_key="bad-key", provider="groq", is_byok=True),
        )

    # Restricted to the one groq-served model in the chain, and never
    # retried past its single auth failure.
    assert p.generate.call_count == 1


def test_min_interval_sleep_constant_is_gone():
    """time.sleep paced one worker. The bucket paces the fleet."""
    import cold_email.workers.shared.constants as c

    assert not hasattr(c, "LLM_MIN_INTERVAL_SECONDS")
