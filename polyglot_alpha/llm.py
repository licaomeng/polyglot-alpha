"""Minimal async LLM wrapper — single-provider (Anthropic) consolidation.

Polyglot-Alpha used to ship three live provider paths (Anthropic direct,
OpenRouter HTTP, Gemini direct). After the OpenRouter swap of 2026-05
every production call already routed to Anthropic Claude Haiku 4.5; the
other paths only added dead code and surface area to maintain. This
module is now the single source of truth for LLM access:

* :class:`LLMCallable` — Protocol every consumer talks to.
* :class:`AnthropicLLM` — the only live implementation (Claude Haiku 4.5
  by default; Sonnet 4.5 for the moderator tier).
* :class:`MockLLM` — deterministic offline stand-in for tests and for
  environments without an ``ANTHROPIC_API_KEY``.
* :func:`make_llm` — factory that returns ``AnthropicLLM`` when the key
  is configured, ``MockLLM`` otherwise.

Future provider swap: to add OpenAI / a new provider:

  1. Write a new class implementing the ``LLMCallable`` protocol
     (an async ``__call__(prompt: str) -> str``).
  2. Add a factory ``lambda`` returning an instance of that class.
  3. Register it in :data:`_LLM_FACTORIES` below.
  4. Set ``LLM_BACKEND=openai`` (or your name) in ``.env``.

Default backend is ``"anthropic"`` — currently the only registered
implementation. The factory falls back to :class:`MockLLM` whenever the
configured backend's API key is missing so unit tests stay offline.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anthropic import AsyncAnthropic as _AsyncAnthropicType

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared AsyncAnthropic singleton
# ---------------------------------------------------------------------------
#
# The Anthropic SDK's ``AsyncAnthropic`` owns an internal ``httpx.AsyncClient``
# that registers an async finalizer. If we instantiate one client per
# ``AnthropicLLM`` and let it get garbage-collected after the FastAPI event
# loop has already closed, the finalizer fires on a dead loop and prints the
# infamous ``RuntimeError: Event loop is closed`` "Task exception was never
# retrieved" traceback.
#
# Strategy A: keep a single module-level ``AsyncAnthropic`` instance that is
# lazily constructed on first use and explicitly ``aclose()``-ed from the
# FastAPI shutdown hook (see :func:`shutdown_anthropic` and
# ``polyglot_alpha.api.main.lifespan``). All ``AnthropicLLM`` instances share
# this client; ``model`` / ``system`` / ``temperature`` differentiation lives
# at the call site, not in the SDK client.

_ANTHROPIC_CLIENT: "_AsyncAnthropicType | None" = None
_ANTHROPIC_CLIENT_LOCK = asyncio.Lock()


def get_anthropic_client(api_key: str | None = None) -> "_AsyncAnthropicType":
    """Return the process-wide shared ``AsyncAnthropic`` instance.

    Lazy-initialised on first call. Subsequent calls reuse the same client
    so its underlying ``httpx.AsyncClient`` is opened exactly once and
    closed exactly once (by :func:`shutdown_anthropic`).

    Raises :class:`LLMError` if the ``anthropic`` SDK isn't installed or
    if no API key is available.
    """

    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is not None:
        return _ANTHROPIC_CLIENT

    try:
        from anthropic import AsyncAnthropic  # type: ignore
    except ImportError as exc:  # pragma: no cover - env error path
        raise LLMError(
            "anthropic SDK not installed; pip install anthropic"
        ) from exc

    resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not resolved_key:
        raise LLMError("ANTHROPIC_API_KEY is not set")

    _ANTHROPIC_CLIENT = AsyncAnthropic(api_key=resolved_key)
    return _ANTHROPIC_CLIENT


async def shutdown_anthropic() -> None:
    """Close the shared ``AsyncAnthropic`` client cleanly.

    Safe to call multiple times and safe to call when the client was
    never initialised. Intended to be wired into FastAPI's shutdown
    lifespan so the underlying ``httpx.AsyncClient`` is closed *before*
    the event loop tears down — avoiding the
    ``RuntimeError: Event loop is closed`` finalizer noise.
    """

    global _ANTHROPIC_CLIENT
    client = _ANTHROPIC_CLIENT
    if client is None:
        return
    _ANTHROPIC_CLIENT = None
    try:
        await client.aclose()
    except Exception as exc:  # pragma: no cover - shutdown best-effort
        LOGGER.warning("AsyncAnthropic.aclose() raised during shutdown: %s", exc)


# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------

# Anthropic direct snapshots. The 4-5 family is the cheapest current Claude
# tier and is what every Polyglot-Alpha LLM call now defaults to.
CLAUDE_HAIKU = "claude-haiku-4-5-20251001"
CLAUDE_SONNET = "claude-sonnet-4-5-20250929"

DEFAULT_TIMEOUT = 30.0

# Per-call defaults — keep ``max_tokens`` conservative so the cheap Haiku
# tier stays cheap. Callers that need more headroom (moderator) bump it
# explicitly.
DEFAULT_MAX_TOKENS = 1024
MODERATOR_MAX_TOKENS = 4000

LLMCallable = Callable[[str], Awaitable[str]]


class LLMError(RuntimeError):
    """Raised when no backend is configured or the live backend fails."""


# ---------------------------------------------------------------------------
# Anthropic concurrency throttle
# ---------------------------------------------------------------------------
#
# All ``AnthropicLLM`` instances share a single module-level
# ``asyncio.Semaphore`` so that the 11-judge panel + seeders cannot burst
# past the account's per-minute request budget (typical personal tier ~=
# 200 RPM, comfortably served by ~5 in-flight). Without this lock the
# ``asyncio.gather`` that fans out MQM + D1/D3/D5/D6/D7 in parallel — plus
# the internal-debate seeders firing concurrently — routinely tripped 429
# with non-trivial ``Retry-After`` spikes (see backend event 159 log).
#
# Lazy init binds the semaphore to whatever event loop first asks for it,
# which keeps unit tests that create their own loops happy. The limit is
# read once on first use and frozen for the process lifetime.

_ANTHROPIC_MAX_CONCURRENCY_DEFAULT = 5
_ANTHROPIC_SEMA: asyncio.Semaphore | None = None
_RETRY_AFTER_FALLBACK_SECONDS = 2.0
_RETRY_AFTER_MAX_RETRIES = 3

# Adaptive-timeout policy: under load (>3 queued waiters) bump the per-call
# wall-clock budget from the base 60s to a higher ceiling so judges queued
# behind a full semaphore don't trip ``asyncio.wait_for`` before they ever
# get a slot. See A2 incident clusters at 05:40:34 / 05:41:07.
_ANTHROPIC_BASE_TIMEOUT_S = 60.0
_ANTHROPIC_LOADED_TIMEOUT_S = 120.0
_ANTHROPIC_QUEUE_DEPTH_THRESHOLD = 3
_ANTHROPIC_INFLIGHT = 0
_ANTHROPIC_QUEUED = 0


def _anthropic_timeout_multiplier() -> float:
    """Return ``ANTHROPIC_TIMEOUT_MULTIPLIER`` parsed as float, default 1.0.

    Lets operators boost every Anthropic per-call timeout during high-load
    demos (e.g. set to ``2.0`` to double everything) without a code change.
    Invalid values silently fall back to ``1.0``.
    """

    raw = os.environ.get("ANTHROPIC_TIMEOUT_MULTIPLIER")
    if not raw:
        return 1.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0


def _effective_anthropic_timeout() -> float:
    """Pick base (60s) vs loaded (120s) timeout based on current queue depth.

    If more than ``_ANTHROPIC_QUEUE_DEPTH_THRESHOLD`` callers are currently
    waiting on the semaphore, the next call gets the loaded budget so a
    judge stuck behind 5+ inflight calls doesn't time out before it ever
    acquires a slot. ``ANTHROPIC_TIMEOUT_MULTIPLIER`` is applied last.
    """

    base = (
        _ANTHROPIC_LOADED_TIMEOUT_S
        if _ANTHROPIC_QUEUED > _ANTHROPIC_QUEUE_DEPTH_THRESHOLD
        else _ANTHROPIC_BASE_TIMEOUT_S
    )
    return base * _anthropic_timeout_multiplier()


def _get_anthropic_semaphore() -> asyncio.Semaphore:
    """Return the shared semaphore that throttles every Anthropic call.

    Module-level (not per-instance) so multiple :class:`AnthropicLLM`
    objects spun up by different judges / seeders all share one
    concurrency budget. Limit comes from ``ANTHROPIC_MAX_CONCURRENCY``
    once on first use; defaults to ``5``.
    """

    global _ANTHROPIC_SEMA
    if _ANTHROPIC_SEMA is None:
        try:
            limit = int(
                os.environ.get(
                    "ANTHROPIC_MAX_CONCURRENCY",
                    str(_ANTHROPIC_MAX_CONCURRENCY_DEFAULT),
                )
            )
        except ValueError:
            limit = _ANTHROPIC_MAX_CONCURRENCY_DEFAULT
        if limit < 1:
            limit = 1
        _ANTHROPIC_SEMA = asyncio.Semaphore(limit)
    return _ANTHROPIC_SEMA


def _reset_anthropic_semaphore_for_tests() -> None:
    """Drop the cached semaphore so the next call re-reads the env var.

    Test-only helper; not exported.
    """

    global _ANTHROPIC_SEMA
    _ANTHROPIC_SEMA = None


def _extract_retry_after_seconds(exc: Exception) -> float | None:
    """Return the ``Retry-After`` header in seconds if the SDK error carries one.

    Accepts numeric (delta-seconds) form directly; falls back to a small
    fixed backoff for HTTP-date form since RFC 7231 date parsing isn't
    worth the dependency here. Returns ``None`` when no header is set so
    the caller can decide whether to back off heuristically.
    """

    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return _RETRY_AFTER_FALLBACK_SECONDS


# ---------------------------------------------------------------------------
# AnthropicLLM — the sole live backend.
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
        # Use the process-wide shared ``AsyncAnthropic`` so the underlying
        # ``httpx.AsyncClient`` is opened once and closed deterministically
        # from ``shutdown_anthropic()`` — instead of being GC'd after the
        # event loop closes, which spams ``RuntimeError: Event loop is
        # closed`` on shutdown.
        self._client = get_anthropic_client(api_key=api_key)
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
        # Throttle every Anthropic SDK call through the shared module
        # semaphore so concurrent judges/seeders don't burst past the
        # account's RPM limit. On 429 we honour the server-provided
        # ``Retry-After`` rather than relying on the SDK's built-in
        # retry, which has historically over-fired (see event 159 log).
        sema = _get_anthropic_semaphore()
        last_exc: Exception | None = None
        global _ANTHROPIC_INFLIGHT, _ANTHROPIC_QUEUED
        for attempt in range(_RETRY_AFTER_MAX_RETRIES + 1):
            _ANTHROPIC_QUEUED += 1
            acquired = False
            try:
                async with sema:
                    _ANTHROPIC_QUEUED -= 1
                    acquired = True
                    _ANTHROPIC_INFLIGHT += 1
                    try:
                        resp = await self._client.messages.create(
                            model=self.model,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            system=system,
                            messages=[{"role": "user", "content": user}],
                        )
                    except Exception as exc:  # noqa: BLE001 - SDK has many error types
                        status = getattr(exc, "status_code", None)
                        if status != 429 or attempt >= _RETRY_AFTER_MAX_RETRIES:
                            raise
                        last_exc = exc
                        retry_after = (
                            _extract_retry_after_seconds(exc)
                            or _RETRY_AFTER_FALLBACK_SECONDS
                        )
                        LOGGER.warning(
                            "Anthropic 429 (attempt %d/%d) — sleeping %.2fs per Retry-After",
                            attempt + 1,
                            _RETRY_AFTER_MAX_RETRIES,
                            retry_after,
                        )
                    else:
                        return resp.content[0].text
            finally:
                if acquired:
                    _ANTHROPIC_INFLIGHT -= 1
                else:
                    _ANTHROPIC_QUEUED -= 1
            # Release the slot before sleeping so other coroutines can
            # make progress while we wait out the server-imposed pause.
            await asyncio.sleep(retry_after)
        # Loop exhausted — re-raise the most recent 429.
        assert last_exc is not None  # for type-checkers
        raise last_exc

    async def __call__(self, prompt: str) -> str:
        """``LLMCallable`` shape used everywhere in the pipeline."""

        return await self.complete(
            system=self._default_system or "You are a helpful assistant.",
            user=prompt,
            max_tokens=self._default_max_tokens,
            temperature=self._default_temperature,
        )


# ---------------------------------------------------------------------------
# MockLLM — offline default for tests + missing-key environments.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Provider registry + factory.
# ---------------------------------------------------------------------------
#
# Future provider swap: to add OpenAI / a new provider:
#   1. Write a new class implementing the LLMCallable protocol
#   2. Add a factory function returning an instance
#   3. Register in _LLM_FACTORIES below
#   4. Set LLM_BACKEND=openai in .env
# Default backend is "anthropic"; current single implementation.

LLM_BACKEND = os.environ.get("LLM_BACKEND", "anthropic").lower()


def _anthropic_factory() -> "AnthropicLLM":
    """Build a default ``AnthropicLLM`` instance bound to Claude Haiku 4.5."""

    return AnthropicLLM()


_LLM_FACTORIES: dict[str, Callable[[], "LLMCallable"]] = {
    "anthropic": _anthropic_factory,
    # "openai": lambda: OpenAILLM(),  # future — write the class + register here.
}


def make_llm(
    model_id: str = CLAUDE_HAIKU,
    *,
    mock: bool = False,
    system: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> LLMCallable:
    """Return an async callable bound to the configured backend.

    Routing:

    * ``mock=True`` -> :class:`MockLLM` (test override).
    * ``LLM_BACKEND`` (default ``"anthropic"``) is looked up in
      :data:`_LLM_FACTORIES`. If the matching provider's key is set the
      live class is instantiated; otherwise we fall back to
      :class:`MockLLM` so unit tests stay offline.

    For Anthropic, ``model_id`` is honoured directly when it already
    looks like a Claude snapshot; otherwise the call routes to
    :data:`CLAUDE_HAIKU` (or :data:`CLAUDE_SONNET` when the slug
    mentions ``sonnet``). This preserves the per-agent
    differentiation contract: seeders pass their model_id but the
    real distinction lives in ``system`` prompt + ``temperature``.
    """

    if mock:
        return MockLLM(model_id=model_id)

    backend = LLM_BACKEND

    if backend == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            return MockLLM(model_id=model_id)
        try:
            return AnthropicLLM(
                model=_resolve_anthropic_model(model_id),
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMError:
            return MockLLM(model_id=model_id)

    factory = _LLM_FACTORIES.get(backend)
    if factory is None:
        LOGGER.warning(
            "LLM_BACKEND=%r is not registered in _LLM_FACTORIES; falling back to MockLLM",
            backend,
        )
        return MockLLM(model_id=model_id)
    try:
        return factory()
    except LLMError:
        return MockLLM(model_id=model_id)


# ---------------------------------------------------------------------------
# Module-level complete() / complete_json() — thin Anthropic-only wrappers
# preserved so existing call sites (corpus/style_guide.py, ingestion/
# cross_reference.py) keep working unchanged. Both honour the same
# ``LLM_BACKEND`` / ``ANTHROPIC_API_KEY`` resolution as :func:`make_llm`,
# with a soft-fail to :class:`MockLLM` when no key is configured.
# ---------------------------------------------------------------------------


async def complete(
    prompt: str,
    *,
    system: str | None = None,
    temperature: float = 0.2,
    response_mime_type: str | None = None,  # accepted for API parity; ignored
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Run a single completion against the configured backend.

    ``response_mime_type`` is accepted for backward-compatibility with the
    legacy Gemini path and is silently ignored — Anthropic Claude is
    instructed via the ``system`` prompt to emit JSON when callers want
    JSON, not via a wire-level MIME hint.
    """

    if not os.getenv("ANTHROPIC_API_KEY"):
        # Offline / no key — return the deterministic mock stub so callers
        # (corpus distillation, cross-reference ingestion) still get a
        # well-shaped string rather than crashing during local dev.
        return await MockLLM(model_id="mock")(prompt)

    try:
        llm = AnthropicLLM(
            model=CLAUDE_HAIKU,
            system=system,
            temperature=temperature,
        )
    except LLMError:
        return await MockLLM(model_id="mock")(prompt)

    return await asyncio.wait_for(
        llm.complete(
            system or "You are a helpful assistant.",
            prompt,
            temperature=temperature,
        ),
        timeout=timeout * _anthropic_timeout_multiplier(),
    )


async def complete_json(prompt: str, **kwargs):
    """Run a completion that is expected to return JSON, and parse it.

    Tolerates ```` ```json ```` fences a model might add despite
    instructions.
    """

    import json as _json

    text = await complete(prompt, response_mime_type="application/json", **kwargs)
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return _json.loads(text)


def _is_anthropic_model(model_id: str) -> bool:
    """Return True if ``model_id`` should be routed to the Anthropic SDK."""

    if not model_id:
        return False
    lowered = model_id.lower()
    return lowered.startswith("claude") or lowered.startswith("anthropic/")


def _resolve_anthropic_model(model_id: str) -> str:
    """Map a model slug onto an Anthropic snapshot (Haiku by default)."""

    if not model_id or not _is_anthropic_model(model_id):
        return CLAUDE_HAIKU
    lowered = model_id.lower()
    if "sonnet" in lowered:
        return CLAUDE_SONNET
    return CLAUDE_HAIKU


__all__ = [
    "AnthropicLLM",
    "CLAUDE_HAIKU",
    "CLAUDE_SONNET",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TIMEOUT",
    "LLM_BACKEND",
    "LLMCallable",
    "LLMError",
    "MODERATOR_MAX_TOKENS",
    "MockLLM",
    "complete",
    "complete_json",
    "get_anthropic_client",
    "make_llm",
    "shutdown_anthropic",
]
