"""Minimal async LLM wrapper.

Default backend is **Anthropic direct** (``ANTHROPIC_API_KEY``), using
Claude Haiku 4.5 as the cheap workhorse and Claude Sonnet 4.5 as the
moderator-tier upgrade. Legacy OpenRouter / Gemini backends are kept
behind explicit opt-in (``POLYGLOT_LLM_BACKEND=openrouter`` or a missing
Anthropic key) so we can roll back if Anthropic is unreachable.

Two surfaces are exposed:

* ``complete`` / ``complete_json`` — high-level helpers that auto-pick a
  backend based on what API key is set. Used by the legacy pipeline.
* ``make_llm(model_id)`` — returns an async callable bound to a specific
  model. Used by the new per-agent code so each translator agent can pin
  itself to its assigned model. Under the Anthropic-default path, the
  model_id is ignored on the wire (every call routes to Haiku 4.5) while
  the seeders still differ via system-prompt + temperature.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable

import httpx

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------

# Anthropic direct snapshots. The 4-5 family is the cheapest current Claude
# tier and is what every Polyglot-Alpha LLM call now defaults to.
CLAUDE_HAIKU = "claude-haiku-4-5-20251001"
CLAUDE_SONNET = "claude-sonnet-4-5-20250929"

# Legacy OpenRouter slugs — kept as importable aliases so older fixtures /
# scripts that imported them keep working. New code should reference the
# Claude constants above instead.
GEMINI_FLASH = "gemini-2.0-flash"
DEEPSEEK_V3 = "deepseek/deepseek-chat"
QWEN_25 = "qwen/qwen-2.5-72b-instruct"
LLAMA_33 = "meta-llama/llama-3.3-70b-instruct"
MISTRAL_LARGE = "mistralai/mistral-large"

DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"
DEFAULT_TIMEOUT = 30.0

# Per-call defaults — keep ``max_tokens`` conservative so the cheap Haiku
# tier stays cheap. Callers that need more headroom (moderator) bump it
# explicitly.
DEFAULT_MAX_TOKENS = 1024
MODERATOR_MAX_TOKENS = 4000

LLMCallable = Callable[[str], Awaitable[str]]


class LLMError(RuntimeError):
    """Raised when no backend is configured or all backends fail."""


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def _backend_preference() -> str:
    """Return ``"anthropic"`` (default), ``"openrouter"`` or ``"gemini"``.

    Order:

    1. ``POLYGLOT_LLM_BACKEND`` env var — explicit override.
    2. ``ANTHROPIC_API_KEY`` set -> ``anthropic`` (NEW DEFAULT).
    3. ``OPENROUTER_API_KEY`` set -> ``openrouter``.
    4. ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` set -> ``gemini``.
    """

    explicit = (os.getenv("POLYGLOT_LLM_BACKEND") or "").strip().lower()
    if explicit in {"anthropic", "openrouter", "gemini"}:
        return explicit
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "gemini"
    return "anthropic"  # caller will get a clear error below


# ---------------------------------------------------------------------------
# AnthropicLLM (new default)
# ---------------------------------------------------------------------------


class AnthropicLLM:
    """Async wrapper around the Anthropic SDK.

    Exposes both an awaitable ``__call__(prompt)`` (so it satisfies the
    ``LLMCallable`` protocol used by every existing pipeline stage) and
    an explicit ``complete(system, user, ...)`` helper for callers that
    want to inject a system prompt or per-call temperature override.
    """

    def __init__(
        self,
        model: str = CLAUDE_HAIKU,
        *,
        api_key: str | None = None,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        # Lazy import so unit tests that monkey-patch ``make_llm`` to a
        # ``MockLLM`` don't pay the SDK import cost / need the SDK
        # installed.
        try:
            from anthropic import AsyncAnthropic  # type: ignore
        except ImportError as exc:  # pragma: no cover - env error path
            raise LLMError(
                "anthropic SDK not installed; pip install anthropic"
            ) from exc

        resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self._client = AsyncAnthropic(api_key=resolved_key)
        self.model = model
        self._default_system = system
        self._default_temperature = temperature
        self._default_max_tokens = max_tokens

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
    ) -> str:
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text

    async def __call__(self, prompt: str) -> str:
        """``LLMCallable`` shape used everywhere in the pipeline."""

        return await self.complete(
            system=self._default_system or "You are a helpful assistant.",
            user=prompt,
            max_tokens=self._default_max_tokens,
            temperature=self._default_temperature,
        )


# ---------------------------------------------------------------------------
# Legacy helpers (complete/complete_json) — now Anthropic-first.
# ---------------------------------------------------------------------------


async def complete(
    prompt: str,
    *,
    system: str | None = None,
    temperature: float = 0.2,
    response_mime_type: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Run a single completion against the preferred backend."""

    backend = _backend_preference()

    if backend == "anthropic":
        try:
            llm = AnthropicLLM(
                model=CLAUDE_HAIKU,
                system=system,
                temperature=temperature,
            )
            return await asyncio.wait_for(
                llm.complete(
                    system or "You are a helpful assistant.",
                    prompt,
                    temperature=temperature,
                ),
                timeout=timeout,
            )
        except LLMError:
            # Fall through to the next backend so callers don't crash on
            # a missing key when an alternative provider is configured.
            pass

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if backend == "gemini" and gemini_key:
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
        "No LLM backend configured. Set ANTHROPIC_API_KEY"
        " (preferred) or OPENROUTER_API_KEY / GEMINI_API_KEY."
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
# Legacy backends (Gemini / OpenRouter) — kept for opt-in only.               #
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


def _is_anthropic_model(model_id: str) -> bool:
    """Return True if ``model_id`` should be routed to the Anthropic SDK."""

    if not model_id:
        return False
    lowered = model_id.lower()
    return lowered.startswith("claude") or lowered.startswith("anthropic/")


def _resolve_anthropic_model(model_id: str) -> str:
    """Map an OpenRouter-style slug (or empty string) onto an Anthropic snapshot."""

    if not model_id or not _is_anthropic_model(model_id):
        return CLAUDE_HAIKU
    lowered = model_id.lower()
    if "sonnet" in lowered:
        return CLAUDE_SONNET
    return CLAUDE_HAIKU


def make_llm(
    model_id: str,
    *,
    mock: bool = False,
    system: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> LLMCallable:
    """Return an async callable bound to ``model_id``.

    Default routing (since the OpenRouter swap):

    * ``ANTHROPIC_API_KEY`` set -> :class:`AnthropicLLM`. The
      ``model_id`` is mapped onto :data:`CLAUDE_HAIKU` (or
      :data:`CLAUDE_SONNET` when the slug mentions ``sonnet``); the
      per-agent differentiation lives in the ``system`` prompt /
      ``temperature`` overrides instead of model selection.
    * ``POLYGLOT_LLM_BACKEND=openrouter`` (or no Anthropic key) ->
      legacy OpenRouter path keyed by ``model_id``.
    * ``model_id.startswith("gemini")`` and ``GEMINI_API_KEY`` set ->
      Google AI Studio.
    * Otherwise -> :class:`MockLLM` so unit tests stay offline.
    """

    if mock:
        return MockLLM(model_id=model_id)

    backend = _backend_preference()

    if backend == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        try:
            return AnthropicLLM(
                model=_resolve_anthropic_model(model_id),
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMError:
            # Fall through to other providers if SDK / key are unusable.
            pass

    if model_id.startswith("gemini"):
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            return MockLLM(model_id=model_id)
        return _gemini_callable(model_id, key)

    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return MockLLM(model_id=model_id)
    return _openrouter_callable(model_id, key)


__all__ = [
    "AnthropicLLM",
    "CLAUDE_HAIKU",
    "CLAUDE_SONNET",
    "DEEPSEEK_V3",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TIMEOUT",
    "GEMINI_FLASH",
    "LLAMA_33",
    "LLMCallable",
    "LLMError",
    "MISTRAL_LARGE",
    "MODERATOR_MAX_TOKENS",
    "MockLLM",
    "QWEN_25",
    "complete",
    "complete_json",
    "make_llm",
]
