import logging
from dataclasses import dataclass
from typing import Protocol

import groq
from google import genai
from google.genai import errors as genai_errors
from groq import Groq
from pydantic import BaseModel

from cold_email.auth.crypto import decrypt
from cold_email.config import settings
from cold_email.workers.shared.constants import BUCKET_WAIT_SECONDS
from cold_email.workers.shared.rate_limit import acquire

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LlmCredentials:
    """Which key a call uses, and whether platform limits apply.

    Three paths, one resolver:
      * platform key -> shared token bucket + per-user quota
      * user's own key (BYOK) -> their limits, so both are bypassed
      * self-hosted -> env keys, bucket harmlessly enforced
    """

    api_key: str | None
    provider: str | None
    is_byok: bool


def resolve_llm_credentials(user) -> LlmCredentials:
    """Pick the credentials for a user's LLM calls."""
    if user is not None and user.llm_api_key_enc:
        return LlmCredentials(
            api_key=decrypt(user.llm_api_key_enc),
            provider=user.llm_provider,
            is_byok=True,
        )
    return LlmCredentials(api_key=None, provider=None, is_byok=False)


def _field_guide(schema: type[BaseModel]) -> str:
    props = schema.model_json_schema().get("properties", {})
    lines = "\n".join(
        f'- "{name}" ({spec.get("type", "any")}): {spec.get("description", "")}'.rstrip()
        for name, spec in props.items()
    )
    return (
        "Respond with ONE JSON object containing EXACTLY these keys — return the "
        "actual values, not this schema, no markdown, no prose:\n" + lines
    )


class LLMProvider(Protocol):
    def generate(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        api_key: str | None = None,
    ) -> str: ...

    def should_fall_back(self, exc: Exception) -> bool: ...

    def is_auth_error(self, exc: Exception) -> bool: ...


class GeminiProvider:
    def generate(self, *, model, system, prompt, schema, api_key: str | None = None) -> str:
        client = genai.Client(api_key=api_key or settings.gemini_api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "system_instruction": system,
                "response_mime_type": "application/json",
                "response_schema": schema,
            },
        )
        return response.text or ""

    def should_fall_back(self, exc: Exception) -> bool:
        if isinstance(exc, genai_errors.APIError) and getattr(exc, "code", None) in (
            404,
            429,
        ):
            return True
        msg = str(exc)
        return "RESOURCE_EXHAUSTED" in msg or "NOT_FOUND" in msg

    def is_auth_error(self, exc: Exception) -> bool:
        if isinstance(exc, genai_errors.APIError) and getattr(exc, "code", None) in (401, 403):
            return True
        msg = str(exc)
        return "API_KEY_INVALID" in msg or "API key not valid" in msg


class GroqProvider:
    def generate(self, *, model, system, prompt, schema, api_key: str | None = None) -> str:
        client = Groq(api_key=api_key or settings.groq_api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": f"{system}\n\n{_field_guide(schema)}"},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    def should_fall_back(self, exc: Exception) -> bool:
        if isinstance(exc, (groq.RateLimitError, groq.NotFoundError)):
            return True
        if isinstance(exc, groq.BadRequestError):
            msg = str(exc)
            return "decommission" in msg or "model_not_found" in msg
        return False

    def is_auth_error(self, exc: Exception) -> bool:
        return isinstance(exc, groq.AuthenticationError)


_GEMINI = GeminiProvider()
_GROQ = GroqProvider()

# Provider-NAME lookup, keyed by the same string a BYOK user picks in
# SetLlmKeyRequest.provider ("groq" | "gemini"). _provider_for keys off the
# model NAME instead (a chain can mix providers); this is the second half
# generate_json needs to answer "does this model belong to THIS user's BYOK
# provider" without hardcoding the prefix rule twice.
_PROVIDERS_BY_NAME: dict[str, LLMProvider] = {"gemini": _GEMINI, "groq": _GROQ}


def _provider_name_for(model: str) -> str:
    """Route a model name to its provider's NAME ("gemini" | "groq").

    The single source of truth for the "gemini*" / "llama*" prefix rule;
    _provider_for and the BYOK chain-restriction in generate_json both key off
    this so the rule is never duplicated.
    """
    if model.startswith("gemini"):
        return "gemini"
    if model.startswith("llama"):
        return "groq"
    raise ValueError(f"Unknown model: {model}")


def _provider_for(model: str) -> LLMProvider:
    """Route a model name to its provider adapter.

    This is the single point that makes "swap models by editing the chain" work:
    given a name like "llama-3.3-70b-versatile" or "gemini-3.5-flash-lite",
    return the right adapter (_GROQ or _GEMINI).
    """
    return _PROVIDERS_BY_NAME[_provider_name_for(model)]


class LlmAuthenticationError(Exception):
    """A provider rejected the API key used for a call.

    Distinct from the generic fall-back-worthy errors: retrying the SAME key
    against the SAME or a different model will never succeed, so a caller
    must treat this as terminal rather than looping on it forever (see
    generate_json's BYOK routing and drafting_task's per-row handling).
    """


def generate_json(
    *,
    system: str,
    prompt: str,
    schema: type[BaseModel],
    credentials: LlmCredentials | None = None,
) -> str:
    """Generate structured JSON, walking the model fallback chain.

    `credentials` gates the shared token bucket: BYOK callers bypass it
    entirely (their key, their limits — the bucket models OUR platform quota,
    not theirs). A bucket timeout is treated exactly like a 429: it skips to
    the next model in the chain, reusing the existing skip logic rather than
    growing a second, parallel notion of "this model is unavailable".

    A BYOK call is also restricted to the models SERVED BY that user's
    provider before the loop ever starts. Without this, `credentials.api_key`
    gets handed to whatever `_provider_for(model)` returns for the FIRST
    model in the chain — e.g. a saved Gemini key handed to Groq first, whose
    AuthenticationError isn't fall-back-worthy, so the call fails immediately
    and the working Gemini entry later in the chain is never tried.

    Raises ValueError up front if the restricted chain is empty (the user's
    provider has no model configured anywhere in the chain) — a clear,
    named failure instead of silently falling through to the platform key.
    """
    credentials = credentials or LlmCredentials(api_key=None, provider=None, is_byok=False)
    chain = settings.model_fallback_chain or [settings.model_name]

    if credentials.is_byok and credentials.provider:
        chain = [m for m in chain if _provider_name_for(m) == credentials.provider]
        if not chain:
            raise ValueError(
                f"BYOK provider '{credentials.provider}' has no model in the configured "
                "fallback chain (MODEL_FALLBACK_CHAIN / MODEL_NAME)."
            )

    last_exc: Exception | None = None

    for model in chain:
        if not credentials.is_byok and not acquire(f"llm:{model}", timeout=BUCKET_WAIT_SECONDS):
            logger.info(f"Token bucket exhausted for {model}; skipping to the next model")
            last_exc = RuntimeError(f"rate limit timeout for {model}")
            continue

        provider = _provider_for(model)
        try:
            return provider.generate(
                model=model,
                system=system,
                prompt=prompt,
                schema=schema,
                api_key=credentials.api_key,
            )
        except Exception as exc:
            if provider.is_auth_error(exc):
                # Terminal, not fall-back-worthy: the same key will never
                # succeed against this OR any other model, so looping the
                # chain (or retrying the Celery task) can't help.
                raise LlmAuthenticationError(
                    f"Authentication failed for model {model!r}: {exc}"
                ) from exc
            if not provider.should_fall_back(exc):
                raise
            last_exc = exc
            logger.warning(
                "Model %s unavailable (%s); falling back to next in chain",
                model,
                type(exc).__name__,
            )

    logger.error("All %d models in fallback chain exhausted", len(chain))
    # pyrefly: ignore [bad-raise]
    raise last_exc
