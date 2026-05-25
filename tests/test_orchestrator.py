"""Tests for ``polyglot_alpha.orchestrator``."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlmodel import Session, select


@pytest.fixture()
def force_judges_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the judge panel adapter to return a deterministic PASS verdict."""

    from polyglot_alpha import orchestrator

    async def passing_judges(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.9, "comet": 0.85, "mqm": {"score": 0}},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.92,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing_judges)


@pytest.mark.asyncio
async def test_run_lifecycle_end_to_end(
    isolated_db: str, sample_event: dict[str, Any], force_judges_pass: None
) -> None:
    """Full happy path: bid -> settle -> translate -> judge PASS -> commit -> Polymarket submit."""

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

    result = await run_lifecycle(
        sample_event,
        auction_window_seconds=0.0,
        mock_bids=[
            BidRecord(agent_address="0xagent_lo", bid_amount=0.5),
            BidRecord(agent_address="0xagent_hi", bid_amount=2.5),
        ],
    )

    # Thesis: lowest qualified bid wins (both bidders use default
    # reputation = 1.0 so both are qualified; lo wins on amount).
    assert result["status"] == EventStatus.SUBMITTED.value
    assert result["winner_address"] == "0xagent_lo"
    assert result["is_simulated"] is True
    assert result["overall_score"] > 0.0

    with Session(engine) as s:
        events = s.exec(select(Event)).all()
        assert len(events) == 1
        bids = s.exec(select(Bid)).all()
        assert {b.agent_address for b in bids} == {"0xagent_lo", "0xagent_hi"}
        auction = s.exec(select(Auction)).one()
        assert auction.winner_address == "0xagent_lo"
        assert s.exec(select(Translation)).one() is not None
        score = s.exec(select(QualityScore)).one()
        assert score.verdict == "PASS"
        assert s.exec(select(Question)).one() is not None
        submission = s.exec(select(PolymarketSubmission)).one()
        assert submission.is_simulated is True
        fees = s.exec(select(BuilderFeeEvent)).all()
        assert len(fees) == 1 and fees[0].is_simulated


@pytest.mark.asyncio
async def test_run_lifecycle_dedup_by_content_hash(
    isolated_db: str, sample_event: dict[str, Any], force_judges_pass: None
) -> None:
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle

    bids = [BidRecord(agent_address="0xagent", bid_amount=1.0)]
    first = await run_lifecycle(
        sample_event, auction_window_seconds=0.0, mock_bids=bids
    )
    second = await run_lifecycle(
        sample_event, auction_window_seconds=0.0, mock_bids=bids
    )
    assert second.get("deduped") is True
    assert second["event_id"] == first["event_id"]


@pytest.mark.asyncio
async def test_run_lifecycle_publishes_sse_events(
    isolated_db: str, sample_event: dict[str, Any], force_judges_pass: None
) -> None:
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.pubsub import get_pubsub

    hub = get_pubsub()
    captured: list[dict[str, Any]] = []

    async def consumer() -> None:
        async with hub.subscribe() as queue:
            try:
                while True:
                    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                    captured.append(msg)
            except asyncio.TimeoutError:
                return

    consumer_task = asyncio.create_task(consumer())
    # let the subscriber register first
    await asyncio.sleep(0.05)

    await run_lifecycle(
        sample_event,
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xagent", bid_amount=1.0)],
    )

    await consumer_task
    types = [m["type"] for m in captured]
    for expected in (
        "event.created",
        "auction.opened",
        "bid.submitted",
        "auction.settled",
        "translation.completed",
        "quality.verdict",
        "onchain.committed",
        "polymarket.submitted",
    ):
        assert expected in types, f"missing SSE event: {expected}"


@pytest.mark.asyncio
async def test_run_lifecycle_rejected_when_quality_fails(
    isolated_db: str, sample_event: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from polyglot_alpha import orchestrator
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import (
        EventStatus,
        PolymarketSubmission,
        Question,
    )

    async def failing_judges(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"j1": 0.1},
            style_alignment_passes={"s1": False},
            overall_score=0.1,
            verdict="FAIL",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", failing_judges)

    result = await run_lifecycle(
        sample_event,
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xagent", bid_amount=1.0)],
    )
    assert result["status"] == EventStatus.REJECTED.value
    assert result["verdict"] == "FAIL"

    with Session(engine) as s:
        assert s.exec(select(Question)).first() is None
        assert s.exec(select(PolymarketSubmission)).first() is None


@pytest.mark.asyncio
async def test_run_lifecycle_updates_reputation(
    isolated_db: str, sample_event: dict[str, Any], force_judges_pass: None
) -> None:
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import AgentReputation

    # Thesis: lowest qualified bid wins. ``0xwinner`` bids the lower
    # amount so it should be the auction winner.
    await run_lifecycle(
        sample_event,
        auction_window_seconds=0.0,
        mock_bids=[
            BidRecord(agent_address="0xwinner", bid_amount=1.0),
            BidRecord(agent_address="0xloser", bid_amount=5.0),
        ],
    )

    with Session(engine) as s:
        winner = s.get(AgentReputation, "0xwinner")
        loser = s.get(AgentReputation, "0xloser")
        assert winner is not None and loser is not None
        assert winner.total_bids == 1
        assert winner.total_wins == 1
        assert winner.avg_quality > 0
        assert winner.cumulative_fees > 0  # simulated fee accrued
        assert loser.total_wins == 0
        assert loser.total_bids == 1


@pytest.mark.asyncio
async def test_run_lifecycle_no_bids_marks_failed(
    isolated_db: str, sample_event: dict[str, Any]
) -> None:
    from polyglot_alpha.orchestrator import run_lifecycle
    from polyglot_alpha.persistence.models import EventStatus

    result = await run_lifecycle(
        sample_event, auction_window_seconds=0.0, mock_bids=[]
    )
    assert result["status"] == EventStatus.FAILED.value
    assert result["reason"] == "no_bids"


def test_compute_content_hash_is_stable() -> None:
    from polyglot_alpha.orchestrator import compute_content_hash

    a = {"title": "x", "sources": [{"url": "u"}], "language": "en"}
    b = {"title": "x", "sources": [{"url": "u"}], "language": "en"}
    c = {"title": "y", "sources": [{"url": "u"}], "language": "en"}
    assert compute_content_hash(a) == compute_content_hash(b)
    assert compute_content_hash(a) != compute_content_hash(c)


def test_lazy_chain_helpers_resolve_real_modules() -> None:
    """The lazy import helpers must surface the real ``chain.*`` modules
    when the package is available, and tolerate missing helpers (return
    ``None``) without raising — that is the contract the orchestrator
    depends on when ``chain/`` is owned by a parallel agent.
    """

    from polyglot_alpha import orchestrator

    auction_client = orchestrator._get_chain_auction_client()
    question_registry = orchestrator._get_chain_question_registry()
    # Real chain package is present in this checkout.
    assert auction_client is not None
    assert question_registry is not None
    # Module surface required by the orchestrator.
    assert hasattr(auction_client, "open_auction")
    assert hasattr(auction_client, "settle_auction")
    assert hasattr(question_registry, "commit_question")


def test_lazy_dispatch_helper_resolves_real_module() -> None:
    from polyglot_alpha import orchestrator

    dispatch = orchestrator._get_dispatch()
    assert dispatch is not None
    assert hasattr(dispatch, "collect_bids_inline")
    assert hasattr(dispatch, "run_for_winner")


@pytest.mark.asyncio
async def test_open_auction_returns_none_when_chain_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the chain helper returns ``None``, ``_open_onchain_auction``
    must yield ``tx_hash=None`` rather than fabricating a sha256 stub.
    """

    from polyglot_alpha import orchestrator

    monkeypatch.setattr(
        orchestrator, "_get_chain_auction_client", lambda: None
    )
    tx_hash = await orchestrator._open_onchain_auction(
        event_id=1, content_hash="deadbeef", auction_mode="real"
    )
    assert tx_hash is None


@pytest.mark.asyncio
async def test_commit_question_returns_none_when_chain_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polyglot_alpha import orchestrator

    monkeypatch.setattr(
        orchestrator, "_get_chain_question_registry", lambda: None
    )
    question_id, tx_hash = await orchestrator._commit_question_onchain(
        event_id=42,
        candidate_hash="deadbeef",
        builder_code="POLYGLOT_TEST",
        pipeline_trace_ipfs="ipfs://test",
        auction_mode="real",
    )
    assert tx_hash is None
    assert question_id.startswith("pending-")
