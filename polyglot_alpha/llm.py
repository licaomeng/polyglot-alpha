"""Minimal async LLM wrapper.

Prefers Gemini (``GEMINI_API_KEY``) when available; falls back to OpenRouter
(``OPENROUTER_API_KEY``). Both backends return raw text; callers are
responsible for parsing JSON when they request a JSON-shaped response.

Two surfaces are exposed:

* ``complete`` / ``complete_json`` — high-level helpers that auto-pick a
  backend based on what API key is set. Used by the legacy pipeline.
* ``make_llm(model_id)`` — returns an async callable bound to a specific
  model. Used by the new per-agent code so each translator agent can pin
  itself to its assigned model (Gemini, DeepSeek, Qwen, Llama).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable

import httpx

LOGGER = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"
DEFAULT_TIMEOUT = 30.0

# Per-agent model identifiers used by ``make_llm``.
GEMINI_FLASH = "gemini-2.0-flash"
DEEPSEEK_V3 = "deepseek/deepseek-chat"
QWEN_25 = "qwen/qwen-2.5-72b-instruct"
LLAMA_33 = "meta-llama/llama-3.3-70b-instruct"
# OpenRouter-hosted Mistral Large — replaces the legacy Gemini Flash slot
# (Gemini's free-tier 429 quota was injecting fake fallback translations
# into the auction; routing the 4th agent through OpenRouter removes that
# failure mode).
MISTRAL_LARGE = "mistralai/mistral-large"

LLMCallable = Callable[[str], Awaitable[str]]


class LLMError(RuntimeError):
    """Raised when no backend is configured or all backends fail."""


async def complete(
    prompt: str,
    *,
    system: str | None = None,
    temperature: float = 0.2,
    response_mime_type: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Run a single completion against the best available backend."""

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gemini_key:
        try:
            return await _gemini_complete(
                gemini_key,
                prompt,
                system=system,
                temperature=temperature,
                response_mime_type=response_mime_type,
                timeout=timeout,
            )
        except Exception as exc:  # pragma: no cover - network failures
            LOGGER.warning("Gemini call failed, falling back: %s", exc)

    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        return await _openrouter_complete(
            openrouter_key,
            prompt,
            system=system,
            temperature=temperature,
            timeout=timeout,
        )

    raise LLMError(
        "No LLM backend configured. Set GEMINI_API_KEY or OPENROUTER_API_KEY."
    )


async def complete_json(prompt: str, **kwargs: Any) -> Any:
    """Run a completion that is expected to return JSON, and parse it."""

    text = await complete(prompt, response_mime_type="application/json", **kwargs)
    text = text.strip()
    if text.startswith("```"):
        # Strip markdown fences if the model wrapped them.
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


# --------------------------------------------------------------------------- #
# Backends.                                                                   #
# --------------------------------------------------------------------------- #


async def _gemini_complete(
    api_key: str,
    prompt: str,
    *,
    system: str | None,
    temperature: float,
    response_mime_type: str | None,
    timeout: float,
) -> str:
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:"
        f"generateContent?key={api_key}"
    )
    contents: list[dict[str, Any]] = []
    if system:
        contents.append({"role": "user", "parts": [{"text": f"[SYSTEM]\n{system}"}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    generation_config: dict[str, Any] = {"temperature": temperature}
    if response_mime_type:
        generation_config["responseMimeType"] = response_mime_type

    payload = {"contents": contents, "generationConfig": generation_config}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


async def _openrouter_complete(
    api_key: str,
    prompt: str,
    *,
    system: str | None,
    temperature: float,
    timeout: float,
) -> str:
    model = os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {"model": model, "messages": messages, "temperature": temperature}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://polyglot-alpha.local",
        "X-Title": "polyglot-alpha-event-watcher",
    }
    url = "https://openrouter.ai/api/v1/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------- #
# Per-agent LLM factory.                                                      #
# --------------------------------------------------------------------------- #


class MockLLM:
    """Deterministic stand-in returned when no API key is configured.

    Tests can inject a custom ``MockLLM`` (or any async callable) into
    :class:`polyglot_alpha.agents.base.BaseTranslatorAgent` via the
    ``llm_factory`` constructor argument.
    """

    def __init__(self, model_id: str, canned_response: str | None = None) -> None:
        self.model_id = model_id
        self.canned_response = canned_response or (
            '{"question_en": "Mock market question for tests?", '
            '"resolution_criteria": "Resolves YES if the test passes by 2026-12-31T23:59:59Z.", '
            '"end_date_iso": "2026-12-31T23:59:59Z", "tags": ["test", "mock"]}'
        )

    async def __call__(self, prompt: str) -> str:
        await asyncio.sleep(0)
        return self.canned_response


def _gemini_callable(model_id: str, api_key: str) -> LLMCallable:
    async def _call(prompt: str) -> str:
        previous = os.environ.get("GEMINI_MODEL")
        os.environ["GEMINI_MODEL"] = model_id
        try:
            return await _gemini_complete(
                api_key,
                prompt,
                system=None,
                temperature=0.2,
                response_mime_type=None,
                timeout=DEFAULT_TIMEOUT,
            )
        finally:
            if previous is None:
                os.environ.pop("GEMINI_MODEL", None)
            else:
                os.environ["GEMINI_MODEL"] = previous

    return _call


def _openrouter_callable(model_id: str, api_key: str) -> LLMCallable:
    async def _call(prompt: str) -> str:
        # Temporarily pin the model via env override so the shared helper
        # routes to the agent-specific model without changing its signature.
        previous = os.environ.get("OPENROUTER_MODEL")
        os.environ["OPENROUTER_MODEL"] = model_id
        try:
            return await _openrouter_complete(
                api_key,
                prompt,
                system=None,
                temperature=0.2,
                timeout=DEFAULT_TIMEOUT,
            )
        finally:
            if previous is None:
                os.environ.pop("OPENROUTER_MODEL", None)
            else:
                os.environ["OPENROUTER_MODEL"] = previous

    return _call


def make_llm(model_id: str, *, mock: bool = False) -> LLMCallable:
    """Return an async callable bound to ``model_id``.

    Routes:

    * ``gemini-*`` -> Google AI Studio (``GEMINI_API_KEY``).
    * anything else -> OpenRouter (``OPENROUTER_API_KEY``).

    Falls back to :class:`MockLLM` whenever ``mock=True`` or the relevant
    API key environment variable is missing. This makes unit tests safe
    and lets the CI demo run offline.
    """

    if mock:
        return MockLLM(model_id=model_id)
    if model_id.startswith("gemini"):
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            return MockLLM(model_id=model_id)
        return _gemini_callable(model_id, key)
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return MockLLM(model_id=model_id)
    return _openrouter_callable(model_id, key)
