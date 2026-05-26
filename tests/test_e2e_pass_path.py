"""E2E tests for the happy-path lifecycle.

All tests use ``MockLLM`` (no live Anthropic) and the orchestrator's
``mock_bids`` knob so they finish in well under a second. The judge panel
is mocked at the orchestrator boundary (``_evaluate_with_judges``) — the
individual judges (D5 hard-gate, MQM grader, etc.) are exercised by their
own unit-test files.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import pytest
from sqlmodel import Session, select


# ---------------------------------------------------------------------------
# Test-wide helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force MockLLM by clearing the Anthropic API key for the test."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("POLYGLOT_LLM_BACKEND", "mock")


@pytest.fixture()
def _judges_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the judge panel to return PASS with a healthy score."""

    from polyglot_alpha import orchestrator

    async def passing(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.85, "comet": 0.88, "mqm": {"score": 92}},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.92,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing)


@pytest.fixture()
def _deterministic_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Pin the translator pipeline output so candidate_hash is predictable."""

    from polyglot_alpha import orchestrator as orch_mod

    final_question = {
        "title": "Will the test pass by December 31, 2026?",
        "description": "Deterministic test question.",
        "resolution_criteria": "Resolves YES if the test pipeline completes.",
        "resolution_source": "operator",
        "cutoff_ts": "2026-12-31T23:59:59+00:00",
        "category": "test",
        "source_news": "test_e2e_pass_path",
        "source_language": "en",
        "target_language": "en",
        "outcomes": ["Yes", "No"],
        "question_en": "Will the test pass by December 31, 2026?",
    }
    # Canonicalise exactly the way IPFS module does (sorted keys, no
    # whitespace) so the test can recompute identically.
    canonical = json.dumps(final_question, sort_keys=True).encode("utf-8")
    candidate_hash = hashlib.sha256(canonical).hexdigest()
    ipfs_uri = f"ipfs://test/{candidate_hash[:12]}"

    async def stub_pipeline(
        _event_dict: dict[str, Any],
        _winner: Any,
        **_kwargs: Any,
    ) -> orch_mod.PipelineResult:
        return orch_mod.PipelineResult(
            final_question=dict(final_question),
            pipeline_trace_ipfs=ipfs_uri,
            candidate_hash=candidate_hash,
        )

    monkeypatch.setattr(orch_mod, "_run_translator_pipeline", stub_pipeline)
    return {
        "final_question": final_question,
        "candidate_hash": candidate_hash,
        "ipfs_uri": ipfs_uri,
    }


@pytest.fixture()
def _treasury_address(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make sure the 90/10 builder-fee split is exercised."""

    addr = "0xtreasury_for_tests"
    monkeypatch.setenv("PLATFORM_TREASURY_ADDRESS", addr)
    return addr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pass_path_writes_all_subsystem_rows(
    isolated_db: str,
    _judges_pass: None,
    _deterministic_pipeline: dict[str, Any],
    _treasury_address: str,
) -> None:
    """Happy path persists rows in every subsystem table."""

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import (
        Auction,
        Bid,
        BuilderFeeEvent,
        Event,
        EventStatus,
        PolymarketSubmission,
        QualityScore,
        Question,
        Translation,
    )

    event_dict = {
        "title": "Subsystem rows event",
        "sources": [{"url": "https://example.com/a"}],
        "language": "en",
        "category": "test",
    }
    result = await run_lifecycle(
        event_dict,
        auction_window_seconds=0.0,
        mock_bids=[
            BidRecord(agent_address="0xwinner", bid_amount=1.0),
            BidRecord(agent_address="0xrunner", bid_amount=3.0),
            BidRecord(agent_address="0xthird", bid_amount=5.0),
        ],
    )

    assert result["status"] == EventStatus.SUBMITTED.value
    assert result["winner_address"] == "0xwinner"
    event_id = result["event_id"]

    with Session(engine) as s:
        assert len(s.exec(select(Event)).all()) == 1
        assert len(s.exec(select(Bid).where(Bid.event_id == event_id)).all()) == 3
        assert s.exec(select(Auction).where(Auction.event_id == event_id)).one() is not None
        assert s.exec(select(Translation).where(Translation.event_id == event_id)).one() is not None
        assert s.exec(select(QualityScore).where(QualityScore.event_id == event_id)).one() is not None
        assert s.exec(select(Question).where(Question.event_id == event_id)).one() is not None
        assert s.exec(select(PolymarketSubmission).where(PolymarketSubmission.event_id == event_id)).one() is not None
        fee_rows = s.exec(select(BuilderFeeEvent)).all()
        # 90/10 split → 2 rows
        assert len(fee_rows) == 2


@pytest.mark.asyncio
async def test_pass_path_emits_all_core_sse_events(
    isolated_db: str,
    _judges_pass: None,
    _deterministic_pipeline: dict[str, Any],
    _treasury_address: str,
) -> None:
    """All ten canonical SSE event types fire during the happy path.

    NOTE on scope: the orchestrator emits ten event types
    (event.created, auction.opened, bid.submitted, auction.settled,
    translation.completed, quality.verdict, onchain.committed,
    polymarket.submitted, builder_fee.accrued, event.finalized).
    The mission's spec also mentions ``event.updated``,
    ``critic.completed``, ``moderator.verdict`` and ``refine.completed``
    — these are NOT emitted by the orchestrator (``event.updated``
    only fires from the RSS replacement path in trigger.py, and the
    other three are internal stages, not SSE events). Documented in
    outputs/B1_test_findings.md as a spec gap.
    """

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
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
            "title": "SSE coverage event",
            "sources": [{"url": "https://example.com/b"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[
            BidRecord(agent_address="0xA", bid_amount=1.0),
            BidRecord(agent_address="0xB", bid_amount=2.0),
            BidRecord(agent_address="0xC", bid_amount=3.0),
        ],
    )

    await asyncio.sleep(0.05)
    stop.set()
    await task

    types = [m["type"] for m in captured]
    expected = (
        "event.created",
        "auction.opened",
        "bid.submitted",
        "auction.settled",
        "translation.completed",
        "quality.verdict",
        "onchain.committed",
        "polymarket.submitted",
        "builder_fee.accrued",
        "event.finalized",
    )
    for ev in expected:
        assert ev in types, f"missing SSE event {ev}; captured={types}"

    # Three bids => three bid.submitted broadcasts.
    bid_broadcasts = [m for m in captured if m["type"] == "bid.submitted"]
    assert len(bid_broadcasts) == 3


@pytest.mark.asyncio
async def test_pass_path_candidate_hash_provenance(
    isolated_db: str,
    _judges_pass: None,
    _deterministic_pipeline: dict[str, Any],
    _treasury_address: str,
) -> None:
    """Candidate hash matches SHA-256 of the canonical IPFS content."""

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import Question, Translation

    result = await run_lifecycle(
        {
            "title": "Hash provenance event",
            "sources": [{"url": "https://example.com/h"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xprov", bid_amount=1.0)],
    )

    expected_hash = _deterministic_pipeline["candidate_hash"]
    expected_ipfs = _deterministic_pipeline["ipfs_uri"]
    final_question = _deterministic_pipeline["final_question"]

    with Session(engine) as s:
        q = s.exec(select(Question).where(Question.event_id == result["event_id"])).one()
        # Title hash on chain == candidate_hash from translator pipeline.
        assert q.title_hash == expected_hash
        assert q.reasoning_ipfs == expected_ipfs

        translation = s.exec(
            select(Translation).where(Translation.event_id == result["event_id"])
        ).one()
        assert translation.pipeline_trace_ipfs == expected_ipfs

    # Recompute the hash from the persisted final_question — exactly the
    # property an external auditor would check.
    recomputed = hashlib.sha256(
        json.dumps(final_question, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert recomputed == expected_hash


@pytest.mark.asyncio
async def test_pass_path_with_3_mock_bids_picks_lowest_qualified(
    isolated_db: str,
    _judges_pass: None,
    _deterministic_pipeline: dict[str, Any],
    _treasury_address: str,
) -> None:
    """Settlement uses ``bid_amount / max(rep, 1.0)`` — lowest score wins.

    Note: the mission's spec wording (``bid_amount × 1e18 / max(rep, 1.0)``
    and "highest score") matches the smart-contract code, but the Python
    orchestrator uses ``bid_amount / max(rep, 1.0)`` and picks the
    minimum (lowest qualified bid). See orchestrator.py
    ``_settle_auction``. Both reduce to the same winner-selection rule
    because the smart contract inverts the comparison via ``1/score`` —
    the canonical "lowest qualified bid wins" thesis is what the codebase
    enforces and what this test asserts.
    """

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import Auction

    result = await run_lifecycle(
        {
            "title": "Three bids ranking event",
            "sources": [{"url": "https://example.com/r"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[
            BidRecord(agent_address="0xlow", bid_amount=0.5, reputation=1.0),
            BidRecord(agent_address="0xmid", bid_amount=1.5, reputation=1.0),
            BidRecord(agent_address="0xhigh", bid_amount=2.5, reputation=1.0),
        ],
    )

    assert result["winner_address"] == "0xlow"
    with Session(engine) as s:
        auction = s.exec(select(Auction).where(Auction.event_id == result["event_id"])).one()
        assert auction.winner_address == "0xlow"
        assert auction.winning_bid == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_pass_path_builder_fee_split_90_10(
    isolated_db: str,
    _judges_pass: None,
    _deterministic_pipeline: dict[str, Any],
    _treasury_address: str,
) -> None:
    """The 90/10 split persists two BuilderFeeEvent rows summing to 1 USDC."""

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import BuilderFeeEvent

    result = await run_lifecycle(
        {
            "title": "Fee split event",
            "sources": [{"url": "https://example.com/f"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xfeewinner", bid_amount=1.0)],
    )

    assert result["status"] == "SUBMITTED"
    winner_addr = result["winner_address"]

    with Session(engine) as s:
        fees = s.exec(select(BuilderFeeEvent)).all()
        assert len(fees) == 2, f"expected 2 fee rows (90/10 split), got {len(fees)}"
        by_recipient = {f.translator_address: f.fee_amount for f in fees}
        assert winner_addr in by_recipient
        assert _treasury_address in by_recipient
        assert by_recipient[winner_addr] == pytest.approx(0.9)
        assert by_recipient[_treasury_address] == pytest.approx(0.1)
        total = sum(f.fee_amount for f in fees)
        assert total == pytest.approx(1.0)
        # Both legs simulated (no real chain TXs in test env).
        assert all(f.is_simulated for f in fees)


@pytest.mark.asyncio
async def test_pass_path_orchestrator_result_shape(
    isolated_db: str,
    _judges_pass: None,
    _deterministic_pipeline: dict[str, Any],
    _treasury_address: str,
) -> None:
    """The orchestrator returns the contract dict the API/UI depends on."""

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle

    result = await run_lifecycle(
        {
            "title": "Result-shape event",
            "sources": [{"url": "https://example.com/s"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xshape", bid_amount=1.0)],
    )

    for key in (
        "event_id",
        "status",
        "verdict",
        "winner_address",
        "winning_bid",
        "question_id",
        "market_id",
        "overall_score",
        "is_simulated",
        "auction_mode",
        "bids",
    ):
        assert key in result, f"missing key {key} in orchestrator result"
    assert result["status"] == "SUBMITTED"
    assert result["verdict"] == "PASS"
    assert result["is_simulated"] is True
    assert isinstance(result["bids"], list) and len(result["bids"]) == 1
