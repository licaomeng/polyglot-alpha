"""Throttle tests for :mod:`polyglot_alpha.llm`.

The 11-judge panel + internal-debate seeders fire many concurrent
Anthropic calls via ``asyncio.gather``. Without a shared semaphore those
bursts blow past the per-account RPM limit and trip cascades of 429s
(see backend event 159 log). These tests pin two invariants:

1. ``AnthropicLLM.complete()`` calls are serialised through a module-level
   semaphore whose size is controlled by ``ANTHROPIC_MAX_CONCURRENCY``
   (default 5). 20 concurrent calls must never have more than the
   configured limit in-flight at once.
2. The semaphore is shared across :class:`AnthropicLLM` instances — two
   separate instances both pull from the same lock.

Both tests stay offline by monkey-patching ``get_anthropic_client`` to
return a fake ``AsyncAnthropic`` whose ``messages.create`` records peak
concurrency.

Run with: ``.venv/bin/pytest -xvs tests/test_llm_throttle.py``
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any

import pytest

from polyglot_alpha import llm as llm_module
from polyglot_alpha.llm import (
    AnthropicLLM,
    _extract_retry_after_seconds,
    _get_anthropic_semaphore,
    _reset_anthropic_semaphore_for_tests,
)


# --------------------------------------------------------------------------- #
# Test doubles                                                                #
# --------------------------------------------------------------------------- #


class _ConcurrencyTrackingFakeClient:
    """Fake ``AsyncAnthropic`` that measures peak in-flight calls.

    Each ``messages.create`` call holds the slot for ``hold_seconds`` so
    concurrent gathers actually overlap; the highest simultaneous count
    is exposed via :attr:`peak`.
    """

    def __init__(self, hold_seconds: float = 0.05) -> None:
        self._hold = hold_seconds
        self._in_flight = 0
        self.peak = 0
        self.calls = 0
        self._lock = asyncio.Lock()
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **_: Any) -> Any:
        async with self._lock:
            self._in_flight += 1
            self.calls += 1
            if self._in_flight > self.peak:
                self.peak = self._in_flight
        try:
            # Hold the slot long enough that the gather() can actually
            # contend on the semaphore.
            await asyncio.sleep(self._hold)
        finally:
            async with self._lock:
                self._in_flight -= 1
        return SimpleNamespace(content=[SimpleNamespace(text="ok")])


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clean_semaphore_state(monkeypatch: pytest.MonkeyPatch):
    """Reset semaphore + env between tests so each gets a fresh limit."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _reset_anthropic_semaphore_for_tests()
    yield
    _reset_anthropic_semaphore_for_tests()


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_default_concurrency_is_five(monkeypatch: pytest.MonkeyPatch) -> None:
    """20 concurrent ``complete()`` calls must observe at most 5 in-flight."""

    monkeypatch.delenv("ANTHROPIC_MAX_CONCURRENCY", raising=False)
    fake = _ConcurrencyTrackingFakeClient(hold_seconds=0.05)
    monkeypatch.setattr(llm_module, "get_anthropic_client", lambda api_key=None: fake)

    llm = AnthropicLLM()
    coros = [llm.complete("sys", f"prompt {i}") for i in range(20)]
    results = await asyncio.gather(*coros)

    assert len(results) == 20
    assert all(r == "ok" for r in results)
    assert fake.calls == 20
    assert fake.peak <= 5, f"observed peak={fake.peak} exceeds default cap of 5"
    # Sanity: the semaphore actually contended (otherwise the test would
    # be a no-op). With 20 calls and a 5-slot lock peak should hit the cap.
    assert fake.peak == 5, f"expected peak=5 under contention, got {fake.peak}"


@pytest.mark.asyncio
async def test_env_var_overrides_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ANTHROPIC_MAX_CONCURRENCY=2`` must clamp peak to 2."""

    monkeypatch.setenv("ANTHROPIC_MAX_CONCURRENCY", "2")
    fake = _ConcurrencyTrackingFakeClient(hold_seconds=0.05)
    monkeypatch.setattr(llm_module, "get_anthropic_client", lambda api_key=None: fake)

    llm = AnthropicLLM()
    await asyncio.gather(*[llm.complete("sys", f"p{i}") for i in range(10)])

    assert fake.peak == 2, f"expected peak=2 with env override, got {fake.peak}"


@pytest.mark.asyncio
async def test_semaphore_is_shared_across_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ``AnthropicLLM`` instances must share the same module-level lock."""

    monkeypatch.setenv("ANTHROPIC_MAX_CONCURRENCY", "3")
    fake = _ConcurrencyTrackingFakeClient(hold_seconds=0.05)
    monkeypatch.setattr(llm_module, "get_anthropic_client", lambda api_key=None: fake)

    a = AnthropicLLM()
    b = AnthropicLLM()
    # Half the calls go through instance A, half through instance B. If
    # the semaphore were per-instance peak could reach 6.
    coros = []
    for i in range(20):
        which = a if i % 2 == 0 else b
        coros.append(which.complete("sys", f"p{i}"))
    await asyncio.gather(*coros)

    assert fake.peak <= 3, (
        f"semaphore is not shared across instances: peak={fake.peak} > 3"
    )


@pytest.mark.asyncio
async def test_semaphore_is_module_level() -> None:
    """Sanity: two ``_get_anthropic_semaphore()`` calls return the same object."""

    sema1 = _get_anthropic_semaphore()
    sema2 = _get_anthropic_semaphore()
    assert sema1 is sema2


# --------------------------------------------------------------------------- #
# Retry-After honouring                                                       #
# --------------------------------------------------------------------------- #


def _make_429(retry_after: str | None) -> Exception:
    """Build a fake Anthropic 429 carrying a configurable ``Retry-After``."""

    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    response = SimpleNamespace(headers=headers, status_code=429)
    exc = RuntimeError("429 rate limit")
    exc.status_code = 429  # type: ignore[attr-defined]
    exc.response = response  # type: ignore[attr-defined]
    return exc


@pytest.mark.asyncio
async def test_retry_after_honoured_on_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 with ``Retry-After: 0.01`` must retry and eventually succeed."""

    class _FlakyClient:
        def __init__(self) -> None:
            self.attempts = 0
            self.messages = SimpleNamespace(create=self._create)

        async def _create(self, **_: Any) -> Any:
            self.attempts += 1
            if self.attempts == 1:
                raise _make_429("0.01")
            return SimpleNamespace(content=[SimpleNamespace(text="recovered")])

    flaky = _FlakyClient()
    monkeypatch.setattr(llm_module, "get_anthropic_client", lambda api_key=None: flaky)

    llm = AnthropicLLM()
    result = await llm.complete("sys", "user")
    assert result == "recovered"
    assert flaky.attempts == 2


def test_extract_retry_after_seconds_numeric() -> None:
    exc = _make_429("3.5")
    assert _extract_retry_after_seconds(exc) == pytest.approx(3.5)


def test_extract_retry_after_seconds_missing() -> None:
    exc = _make_429(None)
    assert _extract_retry_after_seconds(exc) is None


def test_extract_retry_after_seconds_http_date_fallback() -> None:
    # HTTP-date form should fall through to the fixed-fallback delay
    # rather than crash on parsing.
    exc = _make_429("Wed, 21 Oct 2026 07:28:00 GMT")
    assert _extract_retry_after_seconds(exc) == pytest.approx(
        llm_module._RETRY_AFTER_FALLBACK_SECONDS
    )
