import logging
from typing import Protocol

import groq
from google import genai
from google.genai import errors as genai_errors
from groq import Groq
from pydantic import BaseModel

from cold_email.config import settings

logger = logging.getLogger(__name__)


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
    def generate(self, *, model: str, system: str, prompt: str, schema: type[BaseModel]) -> str: ...

    def should_fall_back(self, exc: Exception) -> bool: ...


class GeminiProvider:
    def generate(self, *, model, system, prompt, schema) -> str:
        client = genai.Client(api_key=settings.gemini_api_key)
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


class GroqProvider:
    def generate(self, *, model, system, prompt, schema) -> str:
        client = Groq(api_key=settings.groq_api_key)
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


_GEMINI = GeminiProvider()
_GROQ = GroqProvider()


def _provider_for(model: str) -> LLMProvider:
    """Route a model name to its provider adapter.

    This is the single point that makes "swap models by editing the chain" work:
    given a name like "llama-3.3-70b-versatile" or "gemini-3.5-flash-lite",
    return the right adapter (_GROQ or _GEMINI). Decide how to map names —
    prefix inference ("gemini*" -> _GEMINI, else _GROQ) is simplest; an explicit
    dict is more precise. Raise ValueError for a name you can't route so a typo
    fails loudly instead of silently hitting the wrong provider.
    """
    if model.startswith("gemini"):
        return _GEMINI
    if model.startswith("llama"):
        return _GROQ
    raise ValueError(f"Unknown model: {model}")


def generate_json(*, system: str, prompt: str, schema: type[BaseModel]) -> str:
    chain = settings.model_fallback_chain or [settings.model_name]
    last_exc: Exception | None = None

    for model in chain:
        provider = _provider_for(model)
        try:
            return provider.generate(model=model, system=system, prompt=prompt, schema=schema)
        except Exception as exc:
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
