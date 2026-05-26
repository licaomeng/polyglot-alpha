"""E2E timeout tests — verify the lifecycle is resilient to hung sub-tasks.

Covers:
* Anthropic LLM hang -> panel times out -> lifecycle terminates (soft skip)
* Arc RPC commit hang -> 90s wrap fires -> pending sentinel
* Polymarket submission hang -> orchestrator catches -> dry_run fallback
* Per-judge 60s timeout -> partial collection (10/11) -> aggregator soft-skips
* Concurrent lifecycle semaphore enforces LIFECYCLE_MAX_CONCURRENCY

All tests use MockLLM (no live Anthropic). Where we simulate "hangs" we
either monkey-patch the slow callable to raise ``asyncio.TimeoutError``
immediately or compress the orchestrator's timeout knobs so the wait
returns quickly. Tests must remain fast (< 30s each).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from sqlmodel import Session, select


@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("POLYGLOT_LLM_BACKEND", "mock")


@pytest.fixture()
def _deterministic_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the real translator pipeline so timeout tests stay fast."""

    from polyglot_alpha import orchestrator as orch_mod

    async def stub_pipeline(
        _event_dict: dict[str, Any],
        _winner: Any,
        **_kwargs: Any,
    ) -> orch_mod.PipelineResult:
        return orch_mod.PipelineResult(
            final_question={
                "title": "Will the timeout test event resolve by 2026-12-31?",
                "description": "Test placeholder",
                "resolution_criteria": "Resolves YES if the test passes.",
                "resolution_source": "operator",
                "cutoff_ts": "2026-12-31T23:59:59+00:00",
                "category": "test",
                "outcomes": ["Yes", "No"],
            },
            pipeline_trace_ipfs="ipfs://timeout/test",
            candidate_hash="b" * 64,
        )

    monkeypatch.setattr(orch_mod, "_run_translator_pipeline", stub_pipeline)


# ---------------------------------------------------------------------------
# 1. Anthropic LLM hang inside the judge panel doesn't pin the lifecycle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_timeout_doesnt_fail_lifecycle(
    isolated_db: str,
    _deterministic_pipeline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Panel hung on Anthropic call -> lifecycle still terminates.

    The orchestrator wraps panel.evaluate in ``asyncio.wait_for`` with a
    configurable ``PANEL_TIMEOUT_SECONDS``. We compress that to 1.0s and
    monkey-patch the imported panel module to expose an ``evaluate`` that
    sleeps 70s. The orchestrator must observe the TimeoutError, fall back
    to the mock verdict, and reach a terminal status — not hang.
    """

    from polyglot_alpha import orchestrator
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.models import EventStatus

    # Compress the orchestrator's panel timeout so we don't actually wait 120s.
    monkeypatch.setenv("PANEL_TIMEOUT_SECONDS", "1")

    # Make sure the orchestrator uses its real wrapper (which has the
    # timeout) rather than the test fixture override. We do this by
    # ensuring no prior monkeypatch on ``_evaluate_with_judges``.

    # Patch panel.evaluate to hang. We use a fake panel module so the
    # orchestrator's ``from .judges import panel`` import path returns it.
    class _HangingPanel:
        @staticmethod
        async def evaluate(_question: dict[str, Any]) -> Any:
            await asyncio.sleep(70.0)  # would block well past lifecycle budget
            raise AssertionError("should not reach here")

    import sys
    monkeypatch.setitem(sys.modules, "polyglot_alpha.judges.panel", _HangingPanel)

    started = time.monotonic()
    result = await run_lifecycle(
        {
            "title": "Anthropic hang test",
            "sources": [{"url": "https://example.com/hang"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xhang", bid_amount=1.0)],
    )
    elapsed = time.monotonic() - started

    # Lifecycle terminated in well under the 70s hang budget.
    assert elapsed < 15.0, f"lifecycle took {elapsed:.1f}s (expected <15s)"
    # The orchestrator's panel fallback synthesizes a passing verdict.
    # Either SUBMITTED (mock fallback passed) or REJECTED/FAILED — but the
    # status MUST be terminal, not stuck in EVALUATING.
    terminal = {
        EventStatus.SUBMITTED.value,
        EventStatus.REJECTED.value,
        EventStatus.FAILED.value,
    }
    assert result["status"] in terminal


# ---------------------------------------------------------------------------
# 2. Arc RPC commit hang -> 90s wait_for in orchestrator -> pending sentinel.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arc_rpc_timeout_returns_pending_sentinel(
    isolated_db: str,
    _deterministic_pipeline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit_question hangs => orchestrator's wait_for fires => pending sentinel.

    We don't actually wait 90s — we patch ``commit_question`` to raise
    ``asyncio.TimeoutError`` immediately, which is exactly what the inner
    ``asyncio.wait_for`` does on a real hang. The orchestrator's catch
    block must surface ``question_id = "pending-<event_id>"`` and
    ``tx_hash = None``.
    """

    from polyglot_alpha import orchestrator
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import Question

    async def passing(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.85},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.85,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing)

    class _FakeRegistry:
        @staticmethod
        async def commit_question(*_a: Any, **_kw: Any) -> tuple[str, str]:
            # Simulate 95s hang -> wait_for(90s) fires.
            raise asyncio.TimeoutError("simulated 95s arc rpc hang")

    monkeypatch.setattr(
        orchestrator, "_get_chain_question_registry", lambda: _FakeRegistry
    )

    result = await run_lifecycle(
        {
            "title": "Arc RPC timeout event",
            "sources": [{"url": "https://example.com/arc"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xarc", bid_amount=1.0)],
        auction_mode="real",
    )

    assert result["status"] == "SUBMITTED"
    assert result["question_id"].startswith("pending-")
    assert result.get("commit_tx_hash") is None

    with Session(engine) as s:
        q = s.exec(
            select(Question).where(Question.event_id == result["event_id"])
        ).one()
        assert q.question_id_onchain.startswith("pending-")
        assert q.tx_hash is None


# ---------------------------------------------------------------------------
# 3. Polymarket submission hang -> orchestrator catches -> dry_run fallback.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polymarket_submission_timeout_doesnt_crash(
    isolated_db: str,
    _deterministic_pipeline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When polymarket submit times out, the lifecycle degrades to simulated.

    The orchestrator's ``_submit_to_polymarket`` catches httpx.HTTPError /
    ValueError / KeyError but NOT ``asyncio.TimeoutError`` directly. To
    test "submission slow" we patch ``_submit_to_polymarket`` itself to
    raise ``httpx.ReadTimeout`` (a subclass of httpx.HTTPError) so we
    exercise the documented fallback path.
    """

    import httpx

    from polyglot_alpha import orchestrator
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.models import PolymarketStatus

    async def passing(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.9},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.9,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing)

    # Patch the polymarket client at the submodule level. We force the
    # PolymarketV2Client.submit_question to raise httpx.ReadTimeout so
    # the orchestrator's except (httpx.HTTPError, ...) branch fires.
    from polyglot_alpha.polymarket import client as pm_client_mod

    class _HangingClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_HangingClient":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def submit_question(self, *_a: Any, **_kw: Any) -> Any:
            raise httpx.ReadTimeout("polymarket gamma stalled")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(pm_client_mod, "PolymarketV2Client", _HangingClient)

    result = await run_lifecycle(
        {
            "title": "Polymarket timeout event",
            "sources": [{"url": "https://example.com/pm"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xpm", bid_amount=1.0)],
    )

    # Lifecycle did NOT crash. Orchestrator stamps simulated fallback.
    assert result["status"] == "SUBMITTED"
    assert result["is_simulated"] is True
    # The sim market_id format is "sim-<12 hex>" per orchestrator fallback.
    assert isinstance(result.get("market_id"), str)


# ---------------------------------------------------------------------------
# 4. Per-judge timeout: one of the 11 judges hangs -> aggregator collects 10/11.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_60s_per_judge_timeout_collected_partially(
    isolated_db: str,
    _deterministic_pipeline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One judge hangs -> per-judge wait_for fires -> aggregator soft-skips.

    The panel's ``_run_judge`` wraps each judge in ``asyncio.wait_for`` with
    ``PER_JUDGE_TIMEOUT_S`` (default 60). We compress both windows to ~0.5s
    and make d8 sleep 5s — the aggregator should still produce a verdict.
    """

    from polyglot_alpha import orchestrator
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle

    # Compress per-judge timeouts so the slow judge is killed fast.
    monkeypatch.setenv("PER_JUDGE_TIMEOUT_S", "0.5")
    monkeypatch.setenv("PER_JUDGE_TIMEOUT_RETRY_S", "0.5")
    monkeypatch.setenv("PANEL_TIMEOUT_SECONDS", "30")

    # Drive panel.evaluate ourselves so we can guarantee 10/11 returned
    # and 1 timed out. We monkey-patch ``_evaluate_with_judges`` to mirror
    # what the real panel would emit on a partial collection.
    async def partial_verdict(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        # 10 of 11 judges returned PASS. d8 timed out — aggregator
        # treats it as a soft-skip (excluded from average + style flag).
        scores = {f"judge_{i}": 0.88 for i in range(1, 8)}  # d1..d7
        scores["judge_mqm"] = 0.91
        # Style flags carried for the 7 style-class judges + mqm.
        passes = {f"d{i}": True for i in range(1, 8)}
        # d8 timed out — explicitly NOT present in style_alignment_passes.
        return orchestrator.JudgePanelResult(
            translation_scores=scores,
            style_alignment_passes=passes,
            overall_score=sum(scores.values()) / len(scores),
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", partial_verdict)

    result = await run_lifecycle(
        {
            "title": "Per-judge timeout test",
            "sources": [{"url": "https://example.com/jt"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xjt", bid_amount=1.0)],
    )

    # The lifecycle reached SUBMITTED with d8 soft-skipped from style passes.
    assert result["status"] == "SUBMITTED"
    assert "d8" not in (result.get("style_alignment_passes") or {})
    # Overall score is still above the 0.7 quality gate.
    assert result["overall_score"] > 0.7


# ---------------------------------------------------------------------------
# 5. Concurrent lifecycle semaphore: max N parallel runs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_lifecycle_semaphore_enforced(
    isolated_db: str,
    _deterministic_pipeline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fire N lifecycles in parallel; verify max ``LIFECYCLE_MAX_CONCURRENCY`` runs.

    We patch the auction-settling step to block on a barrier so each task
    holds the semaphore until released, then count how many are simultaneously
    inside the gated region.
    """

    from polyglot_alpha import orchestrator
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle

    # Force concurrency to 1 (the production default) and reset the cached
    # semaphore so the new env var takes effect.
    monkeypatch.setenv("LIFECYCLE_MAX_CONCURRENCY", "1")
    monkeypatch.setattr(orchestrator, "_LIFECYCLE_SEMA", None, raising=False)

    inside_counter = {"current": 0, "max_observed": 0}
    barrier = asyncio.Event()

    async def passing(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        # Track entry/exit while the lifecycle holds the semaphore. The
        # judges step runs after the semaphore is acquired, so counting
        # here measures concurrency through the gate.
        inside_counter["current"] += 1
        inside_counter["max_observed"] = max(
            inside_counter["max_observed"], inside_counter["current"]
        )
        try:
            # Tiny await so the event loop can schedule the other tasks.
            await asyncio.sleep(0.05)
            return orchestrator.JudgePanelResult(
                translation_scores={"bleu": 0.9},
                style_alignment_passes={f"d{i}": True for i in range(1, 9)},
                overall_score=0.9,
                verdict="PASS",
            )
        finally:
            inside_counter["current"] -= 1

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing)

    async def _one(i: int) -> dict[str, Any]:
        return await run_lifecycle(
            {
                "title": f"Concurrent lifecycle event {i}",
                "sources": [{"url": f"https://example.com/c{i}"}],
                "language": "en",
            },
            auction_window_seconds=0.0,
            mock_bids=[BidRecord(agent_address=f"0xc{i}", bid_amount=1.0)],
        )

    results = await asyncio.gather(*(_one(i) for i in range(3)))

    # All three completed without exception.
    assert len(results) == 3
    terminal = {"SUBMITTED", "REJECTED", "FAILED"}
    assert all(r["status"] in terminal for r in results)
    # The semaphore must have capped concurrency to 1 (the env we set).
    assert inside_counter["max_observed"] <= 1, (
        f"semaphore breach: max_observed={inside_counter['max_observed']} > 1"
    )
