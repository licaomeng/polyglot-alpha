"""E2E tests for failure paths in the lifecycle.

Covers:
* Hard-gate (D5) judge failure -> REJECTED
* Low MQM score -> REJECTED
* No bids -> FAILED with reason='no_bids' (no synthetic fallback)
* All bidders below reputation gate -> orchestrator's documented fallback
* On-chain commit hang -> 90s timeout -> pending sentinel

All tests use MockLLM (no live Anthropic) and the orchestrator's
``mock_bids`` knob.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlmodel import Session, select


@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("POLYGLOT_LLM_BACKEND", "mock")


@pytest.fixture()
def _deterministic_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the real translator pipeline so failure tests stay fast."""

    from polyglot_alpha import orchestrator as orch_mod

    async def stub_pipeline(
        _event_dict: dict[str, Any],
        _winner: Any,
        **_kwargs: Any,
    ) -> orch_mod.PipelineResult:
        return orch_mod.PipelineResult(
            final_question={
                "title": "Will the fail-path test trigger by December 31, 2026?",
                "description": "Test placeholder",
                "resolution_criteria": "Resolves YES if test passes.",
                "resolution_source": "operator",
                "cutoff_ts": "2026-12-31T23:59:59+00:00",
                "category": "test",
                "outcomes": ["Yes", "No"],
            },
            pipeline_trace_ipfs="ipfs://fail/test",
            candidate_hash="a" * 64,
        )

    monkeypatch.setattr(orch_mod, "_run_translator_pipeline", stub_pipeline)


@pytest.mark.asyncio
async def test_d5_hard_gate_failure_marks_rejected(
    isolated_db: str,
    _deterministic_pipeline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the D5 hard gate fails the panel verdict is FAIL → status=REJECTED.

    The orchestrator only observes the aggregated ``JudgePanelResult``;
    the per-judge gate logic lives inside ``judges.panel``. We simulate a
    D5 failure by returning verdict=FAIL with a missing D5 pass flag —
    this is the same payload the real panel would produce when D5 vetoes
    the candidate.
    """

    from polyglot_alpha import orchestrator
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import (
        EventStatus,
        PolymarketSubmission,
        Question,
        QualityScore,
    )

    async def d5_fails(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.9, "comet": 0.92, "mqm": {"score": 95}},
            # D5 is False — the resolution-clarity hard gate has vetoed.
            style_alignment_passes={
                "d1": True, "d2": True, "d3": True, "d4": True,
                "d5": False,  # <-- hard gate failure
                "d6": True, "d7": True, "d8": True,
            },
            overall_score=0.85,
            verdict="FAIL",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", d5_fails)

    result = await run_lifecycle(
        {
            "title": "D5 hard gate failure event",
            "sources": [{"url": "https://example.com/d5"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xagent", bid_amount=1.0)],
    )

    assert result["status"] == EventStatus.REJECTED.value
    assert result["verdict"] == "FAIL"

    with Session(engine) as s:
        # The QualityScore row should record the FAIL verdict.
        score = s.exec(select(QualityScore).where(QualityScore.event_id == result["event_id"])).one()
        assert score.verdict == "FAIL"
        # Downstream rows must NOT exist — commit / Polymarket skipped.
        assert s.exec(select(Question).where(Question.event_id == result["event_id"])).first() is None
        assert s.exec(select(PolymarketSubmission).where(PolymarketSubmission.event_id == result["event_id"])).first() is None


@pytest.mark.asyncio
async def test_low_mqm_marks_rejected(
    isolated_db: str,
    _deterministic_pipeline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An MQM score below 80 results in FAIL → REJECTED."""

    from polyglot_alpha import orchestrator
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import EventStatus, QualityScore

    async def low_mqm(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        # MQM score 65 — below the 80 threshold the panel uses.
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.6, "comet": 0.55, "mqm": {"score": 65}},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.55,
            verdict="FAIL",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", low_mqm)

    result = await run_lifecycle(
        {
            "title": "Low MQM event",
            "sources": [{"url": "https://example.com/mqm"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xmqm", bid_amount=1.0)],
    )

    assert result["status"] == EventStatus.REJECTED.value
    assert result["overall_score"] < 0.7  # below QUALITY_PASS_THRESHOLD
    with Session(engine) as s:
        score = s.exec(select(QualityScore).where(QualityScore.event_id == result["event_id"])).one()
        assert score.verdict == "FAIL"


@pytest.mark.asyncio
async def test_no_bids_marks_failed_with_reason(
    isolated_db: str,
) -> None:
    """Empty ``mock_bids=[]`` => status=FAILED, reason=no_bids, no fallback."""

    from polyglot_alpha.orchestrator import run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import (
        Bid,
        EventStatus,
        QualityScore,
        Translation,
    )

    result = await run_lifecycle(
        {
            "title": "No bids event",
            "sources": [{"url": "https://example.com/none"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[],
    )

    assert result["status"] == EventStatus.FAILED.value
    assert result.get("reason") == "no_bids"
    # No synthetic mock-fallback fires: no bid, translation or quality rows.
    with Session(engine) as s:
        assert s.exec(select(Bid)).first() is None
        assert s.exec(select(Translation)).first() is None
        assert s.exec(select(QualityScore)).first() is None


@pytest.mark.asyncio
async def test_no_bids_emits_auction_failed_and_event_finalized(
    isolated_db: str,
) -> None:
    """No-bid path publishes ``auction.failed`` + ``event.finalized`` SSE."""

    from polyglot_alpha.orchestrator import run_lifecycle
    from polyglot_alpha.pubsub import get_pubsub

    hub = get_pubsub()
    captured: list[dict[str, Any]] = []
    started = asyncio.Event()
    stop = asyncio.Event()

    async def consumer() -> None:
        async with hub.subscribe() as queue:
            started.set()
            while True:
                if stop.is_set():
                    while True:
                        try:
                            captured.append(queue.get_nowait())
                        except asyncio.QueueEmpty:
                            return
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.2)
                    captured.append(msg)
                except asyncio.TimeoutError:
                    continue

    task = asyncio.create_task(consumer())
    await started.wait()

    await run_lifecycle(
        {
            "title": "No bids SSE event",
            "sources": [],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[],
    )

    await asyncio.sleep(0.05)
    stop.set()
    await task

    types = [m["type"] for m in captured]
    assert "auction.failed" in types
    assert "event.finalized" in types
    finalized = [m for m in captured if m["type"] == "event.finalized"][0]
    assert finalized["data"]["terminal_status"] == "FAILED"
    assert finalized["data"]["reason"] == "no_bids"


@pytest.mark.asyncio
async def test_low_reputation_falls_back_to_raw_lowest(
    isolated_db: str,
    _deterministic_pipeline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When all bidders are below the reputation gate, fall back to raw-lowest.

    Per ``_settle_auction`` documentation: a bid is "qualified" if
    ``reputation >= MIN_QUALIFIED_REPUTATION`` (0.7). If no bid is
    qualified, the orchestrator falls back to the lowest raw bid so the
    lifecycle still completes — this test pins that contract.
    """

    from polyglot_alpha import orchestrator
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle

    async def passing(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.85},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.85,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing)

    result = await run_lifecycle(
        {
            "title": "Low reputation fallback event",
            "sources": [{"url": "https://example.com/rep"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[
            # All below the 0.7 gate — orchestrator must still pick a winner.
            BidRecord(agent_address="0xlow_rep_lo", bid_amount=0.5, reputation=0.1),
            BidRecord(agent_address="0xlow_rep_mid", bid_amount=1.5, reputation=0.3),
            BidRecord(agent_address="0xlow_rep_hi", bid_amount=2.5, reputation=0.5),
        ],
    )

    # Lifecycle completes; winner is the raw-lowest amount.
    assert result["status"] == "SUBMITTED"
    assert result["winner_address"] == "0xlow_rep_lo"


@pytest.mark.asyncio
async def test_chain_commit_timeout_returns_pending(
    isolated_db: str,
    _deterministic_pipeline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``commit_question`` hangs past 90s the orchestrator returns pending.

    To keep the test fast we patch the hard-coded 90s ``asyncio.wait_for``
    used by ``_commit_question_onchain`` by mocking ``commit_question``
    itself to raise ``asyncio.TimeoutError`` immediately — exercising the
    same fallback branch the orchestrator uses on a real chain hang.
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
        async def commit_question(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
            # Simulate the wait_for inside _commit_question_onchain firing.
            raise asyncio.TimeoutError("simulated 90s hang")

    monkeypatch.setattr(
        orchestrator, "_get_chain_question_registry", lambda: _FakeRegistry
    )

    # Force ``auction_mode='real'`` so the orchestrator actually delegates
    # to ``_get_chain_question_registry`` instead of the mock branch.
    result = await run_lifecycle(
        {
            "title": "Commit timeout event",
            "sources": [{"url": "https://example.com/timeout"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xt", bid_amount=1.0, reputation=1.0)],
        auction_mode="real",
    )

    # The lifecycle still reaches SUBMITTED — the orchestrator records
    # ``question_id = "pending-<event_id>"`` and ``tx_hash = None`` rather
    # than failing the whole event.
    assert result["status"] == "SUBMITTED"
    assert result["question_id"].startswith("pending-")
    assert result.get("commit_tx_hash") is None

    with Session(engine) as s:
        q = s.exec(select(Question).where(Question.event_id == result["event_id"])).one()
        assert q.question_id_onchain.startswith("pending-")
        assert q.tx_hash is None
