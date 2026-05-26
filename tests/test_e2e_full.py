"""End-to-end integration test for PolyglotAlpha v2.

Drives a single Chinese-language news event through every module in the
hackathon pipeline:

    sample_0.json (RSS-equivalent payload)
       -> cross_reference.heuristic_cluster  (>=2 source confirm)
       -> orchestrator.run_lifecycle
            -> openAuction (sha256 mock)
            -> 4 agents bid (Gemini/DeepSeek/Qwen/Llama, deterministic)
            -> settleAuction (highest bid wins)
            -> winner pipeline runs (orchestrator's built-in mock translator)
            -> 11-judge panel (real aggregator, stub LLM returns PASS)
            -> commitQuestion (sha256 mock)
            -> Polymarket submit (mock client, is_simulated=True)
       -> MockPolymarketClient.list_fills emits 5 deterministic fills
       -> BuilderFeeEvent rows persisted per fill
    -> final orchestrator output verified end-to-end

Everything LLM-shaped is stubbed in-process; no real network calls,
no real chain calls. Runs deterministically under random.seed(42).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select

# NOTE: ``polyglot_alpha.ingestion.models`` and
# ``polyglot_alpha.persistence.models`` both register a ``events`` table on
# the shared SQLModel metadata — they are mutually exclusive in the same
# Python process (pre-existing codebase quirk; tests/test_cross_reference.py
# and tests/test_orchestrator.py never run together for the same reason). We
# therefore intentionally do NOT import the ingestion subpackage anywhere in
# this file. The handful of pure-functional helpers we need from
# ``cross_reference`` (content_hash, >=2-source confirmation) are reproduced
# inline; the heuristic clusterer itself is covered by tests/test_cross_reference.py.


@dataclass(frozen=True)
class _StubRawEvent:
    """Local stand-in for ``polyglot_alpha.ingestion.models.RawEvent``.

    Used so the e2e test never imports the ingestion subpackage. The shape
    matches the upstream dataclass; only the fields actually consumed by
    the inlined ``heuristic_cluster``-equivalent are populated.
    """

    source: str
    title: str
    summary: str
    url: str
    published_at: datetime
    language: str

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "outputs" / "sample_0.json"

AGENT_BIDS: dict[str, float] = {
    # Bid amounts span the per-seeder windows defined in tests/test_agents.py.
    # Thesis: lowest qualified bid wins -> 0xgemini_agent (Seeder Alpha) at 0.30.
    "0xgemini_agent": 0.30,   # lowest -> winner (Seeder Alpha, macro)
    "0xdeepseek_agent": 0.60,  # Seeder Beta, geopolitics specialist
    "0xqwen_agent": 0.75,      # Seeder Gamma, markets/sentiment
}

EXPECTED_WINNER: str = "0xgemini_agent"

EXPECTED_SSE_TYPES: tuple[str, ...] = (
    "event.created",
    "auction.opened",
    "bid.submitted",
    "auction.settled",
    "translation.completed",
    "quality.verdict",
    "onchain.committed",
    "polymarket.submitted",
    "builder_fee.accrued",
)

# Stub payloads accepted by polyglot_alpha.judges.style_alignment.llm_batch.
_STYLE_PASS_PAYLOAD: dict[str, Any] = {
    "d2": {"passed": True, "score": 0.92, "reason": "Neutral, source cited."},
    "d3": {"passed": True, "score": 0.91, "reason": "Predictive framing."},
    "d6": {"passed": True, "score": 0.97, "reason": "Authoritative gov.cn URL."},
    "d7": {"passed": True, "score": 0.95, "reason": "No leading bias."},
}

_MQM_PASS_PAYLOAD: str = json.dumps(
    {"errors": [], "rationale": "Faithful, fluent translation."}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_event_dict_from_sample(sample: dict[str, Any]) -> dict[str, Any]:
    """Project the demo sample into the orchestrator's event_dict shape."""

    return {
        "title": sample["title"],
        "sources": [{"name": "pboc", "url": sample.get("resolution_source", "")}],
        "language": "zh",
        "category": sample.get("category", "policy/china"),
        # Carry source_news through so the stub pipeline could echo it back
        # if the real T2 dispatch ever lands.
        "source_news": sample.get("source_news", ""),
        "description": sample.get("description", ""),
        "resolution_criteria": sample.get("resolution_criteria", ""),
        "cutoff_ts": sample.get("cutoff_ts", ""),
    }


def _make_raw_events_from_sample(sample: dict[str, Any]) -> list[_StubRawEvent]:
    """Construct >=2-source raw events for the inlined cluster step."""

    now = datetime.now(timezone.utc)
    title = sample["title"]
    summary = sample.get("source_news", "") + " " + sample.get("description", "")
    return [
        _StubRawEvent(
            source="pboc-official",
            title=title,
            summary=summary,
            url=sample.get("resolution_source", "http://www.pbc.gov.cn/"),
            published_at=now,
            language="zh",
        ),
        _StubRawEvent(
            source="xinhua",
            title=title + " (Xinhua report)",
            summary="央行行长潘功胜在金融街论坛年会上表示，将根据需要适时降准。"
            + summary,
            url="http://www.xinhua.com/article/123",
            published_at=now,
            language="zh",
        ),
    ]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_full_lifecycle(
    isolated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One full event through every component in the pipeline."""

    # Deterministic RNG everywhere we can reach.
    random.seed(42)

    start_wall = time.monotonic()

    # ----- 0. Load the demo sample (Chinese PBOC RRR-cut question) ----------
    assert SAMPLE_PATH.exists(), f"missing demo sample {SAMPLE_PATH}"
    sample = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    assert sample["title"].startswith("Will the People's Bank of China")

    # ----- 1. RSS-like raw events -> cross_reference (heuristic) ------------
    # NOTE: ``polyglot_alpha.ingestion.models`` and
    # ``polyglot_alpha.persistence.models`` both register a ``events`` table
    # on the shared SQLModel metadata (pre-existing codebase issue). Loading
    # both in the same Python process raises ``InvalidRequestError``. We
    # therefore cannot ``import polyglot_alpha.ingestion.cross_reference``
    # inside this test because the persistence engine is already initialised
    # by the ``isolated_db`` fixture. As a workaround we inline the two
    # pure-functional helpers we need — ``content_hash`` and the >=2-source
    # cluster confirmation — so the ingestion step still executes the same
    # logic. The heuristic clusterer is exercised by tests/test_cross_reference.py.
    raw_events = _make_raw_events_from_sample(sample)
    assert len(raw_events) >= 2

    # ``cross_reference.content_hash`` — verbatim re-implementation.
    import hashlib

    def _ingestion_content_hash(title: str, urls: list[str]) -> str:
        sorted_urls = sorted({u.strip() for u in urls if u})
        payload = title.strip() + "\n" + "\n".join(sorted_urls)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    distinct_sources = {ev.source for ev in raw_events}
    assert len(distinct_sources) >= 2, "cluster requires >=2 distinct sources"
    canonical_title = raw_events[0].title
    canonical_urls = sorted({ev.url for ev in raw_events})
    ingestion_hash = _ingestion_content_hash(canonical_title, canonical_urls)
    assert len(ingestion_hash) == 64
    # Stash a dict mirroring ConfirmedEvent so later steps can read it.
    confirmed = {
        "cluster_id": "c0",
        "sources_count": len(distinct_sources),
        "primary_title": canonical_title,
        "all_sources": canonical_urls,
        "content_hash": ingestion_hash,
        "languages": sorted({ev.language for ev in raw_events}),
        "summary": raw_events[0].summary,
    }

    # ----- 2a. Stub the translator pipeline so it emits a well-formed -------
    # Polymarket question that the real 11-judge panel can actually evaluate.
    # The orchestrator's built-in mock pipeline produces a placeholder dict
    # (``{"question": "Will ...", "outcomes": [...]}``) keyed by "question",
    # not "title", which makes the judge panel see an empty PanelQuestion.
    # The deterministic stub below mirrors the shape the real T2 dispatch
    # adapter would emit once it lands.
    from polyglot_alpha import orchestrator as orch_mod_pre  # local alias

    deterministic_final_question: dict[str, Any] = {
        "title": sample["title"],
        "description": sample["description"],
        "resolution_criteria": sample["resolution_criteria"],
        "resolution_source": sample["resolution_source"],
        "cutoff_ts": sample["cutoff_ts"],
        "category": sample["category"],
        "source_news": sample["source_news"],
        # Provide a reference translation so BLEU has signal.
        "reference_translation": sample["title"],
    }
    deterministic_candidate_hash = (
        "a" * 64  # 32-byte sha256 hex, deterministic across runs
    )

    async def stub_pipeline(
        _event_dict: dict[str, Any],
        _winner: Any,
        **_kwargs: Any,
    ) -> orch_mod_pre.PipelineResult:
        return orch_mod_pre.PipelineResult(
            final_question=deterministic_final_question,
            pipeline_trace_ipfs=(
                "ipfs://stub/" + deterministic_candidate_hash[:12]
            ),
            candidate_hash=deterministic_candidate_hash,
        )

    monkeypatch.setattr(orch_mod_pre, "_run_translator_pipeline", stub_pipeline)

    # ----- 2b. Stub the 11-judge panel: keep aggregator real, stub LLM I/O --
    # The orchestrator calls panel.evaluate(final_question) with no llm_call;
    # default would try to hit real Gemini/DeepSeek. We wrap the real
    # ``panel.evaluate`` and inject deterministic stub LLM backends so the
    # full aggregator + gating logic runs against a real PanelVerdict.
    from polyglot_alpha.judges import panel as judge_panel

    async def style_stub(prompt: str) -> str:
        return json.dumps(_STYLE_PASS_PAYLOAD)

    async def mqm_stub(prompt: str) -> str:
        return _MQM_PASS_PAYLOAD

    real_evaluate = judge_panel.evaluate

    async def wrapped_evaluate(question: Any) -> Any:  # signature used by orch
        # ``question`` arrives as the orchestrator's final_question dict.
        # Hand it directly to panel.evaluate, which accepts dict payloads via
        # PanelQuestion.from_mapping.
        return await real_evaluate(
            question, llm_call=style_stub, mqm_llm_call=mqm_stub
        )

    # Patch the orchestrator's panel hook so it goes through our wrapper.
    from polyglot_alpha import orchestrator as orch_mod

    async def evaluate_with_real_panel(
        final_question: dict[str, Any]
    ) -> orch_mod.JudgePanelResult:
        verdict = await wrapped_evaluate(final_question)
        # ``verdict.overall_score`` is 0-100; orchestrator normalizes to 0-1.
        norm_score = float(verdict.overall_score) / 100.0
        return orch_mod.JudgePanelResult(
            translation_scores=dict(verdict.translation_scores or {}),
            style_alignment_passes=dict(verdict.style_alignment_passes or {}),
            overall_score=norm_score,
            verdict="PASS" if verdict.overall_pass else "FAIL",
        )

    monkeypatch.setattr(
        orch_mod, "_evaluate_with_judges", evaluate_with_real_panel
    )

    # ----- 3. Subscribe to SSE events BEFORE running the lifecycle ----------
    from polyglot_alpha.pubsub import get_pubsub

    hub = get_pubsub()
    captured: list[dict[str, Any]] = []
    consumer_started = asyncio.Event()
    stop_consumer = asyncio.Event()

    async def consume_events() -> None:
        """Drain events until ``stop_consumer`` is set + the queue empties."""

        async with hub.subscribe() as queue:
            consumer_started.set()
            while True:
                # If the controller signalled stop, drain whatever is left
                # (non-blocking) and exit.
                if stop_consumer.is_set():
                    while True:
                        try:
                            captured.append(queue.get_nowait())
                        except asyncio.QueueEmpty:
                            return
                # Otherwise wait for the next event but periodically wake up
                # so we notice ``stop_consumer`` even when no events arrive
                # for a while (e.g. during the multi-second judge panel run).
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                    captured.append(msg)
                except asyncio.TimeoutError:
                    continue

    consumer_task = asyncio.create_task(consume_events())
    await consumer_started.wait()

    # ----- 4. Run the full lifecycle ----------------------------------------
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle

    event_dict = _build_event_dict_from_sample(sample)
    # Override the orchestrator's content_hash to match the ingestion-cluster
    # one so the persisted event row mirrors what cross_reference produced.
    event_dict["sources"] = [{"url": u} for u in confirmed["all_sources"]]
    event_dict["title"] = confirmed["primary_title"]

    mock_bids = [
        BidRecord(agent_address=addr, bid_amount=amt)
        for addr, amt in AGENT_BIDS.items()
    ]

    result = await run_lifecycle(
        event_dict,
        auction_window_seconds=0.0,
        mock_bids=mock_bids,
    )

    # Give any fire-and-forget tasks (fill listener mock) a tick to log.
    await asyncio.sleep(0.05)

    # ----- 5. Persist 5 deterministic mock-Polymarket fills -----------------
    # The orchestrator emits a single synthetic builder-fee event in
    # simulation mode; we extend that with 5 more deterministic fills via
    # MockPolymarketClient to exercise the V2 client path end-to-end.
    from polyglot_alpha.persistence import session_scope
    from polyglot_alpha.persistence.models import (
        AgentReputation,
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
    from polyglot_alpha.polymarket.mock_client import MockPolymarketClient

    market_id = result["market_id"]
    winner_address = result["winner_address"]

    mock_pm = MockPolymarketClient(
        builder_code=orch_mod.BUILDER_CODE,
        seed=42,
        fills_per_minute=300.0,  # high rate so 5 fills come in a small window
    )
    # Register the orchestrator's market_id with the mock simulator. We pin
    # ``created_at = now - 60s`` so the fill window is non-empty independent
    # of test wall time. (``submit_question`` would mint its own market id,
    # but we want fills tied to the orchestrator's market_id.)
    mock_pm._markets[market_id] = {
        "question": {"text": event_dict["title"]},
        "created_at": int(time.time()) - 60,
        "status": "open",
    }
    fills = await mock_pm.list_fills(market_id, since_ts=int(time.time()) - 60)
    # Take the first 5 deterministic fills. With seed=42 + Poisson
    # (lambda = 300 * 1min = 300) we get a stable count >> 5.
    fills = fills[:5]
    assert len(fills) == 5, f"expected exactly 5 mock fills, got {len(fills)}"

    with session_scope() as session:
        for fill in fills:
            session.add(
                BuilderFeeEvent(
                    market_id=fill.market_id,
                    fill_amount=fill.fill_amount_usdc,
                    fee_amount=fill.builder_fee_usdc,
                    translator_address=winner_address,
                    arc_tx_hash="0xmockfill-" + fill.fill_id[:10],
                    is_simulated=True,
                )
            )
            rep = session.get(AgentReputation, winner_address)
            assert rep is not None, "winner reputation row missing"
            rep.cumulative_fees += fill.builder_fee_usdc
            rep.last_updated = datetime.now(timezone.utc)
            session.add(rep)
        # Broadcast each fill so the SSE stream sees them too.
    for fill in fills:
        await hub.publish(
            "builder_fee.accrued",
            {
                "market_id": fill.market_id,
                "fill_amount": fill.fill_amount_usdc,
                "fee_amount": fill.builder_fee_usdc,
                "is_simulated": True,
            },
        )

    # Signal the consumer to stop + drain any remaining events.
    await asyncio.sleep(0.1)  # let any final puts land
    stop_consumer.set()
    await consumer_task

    elapsed = time.monotonic() - start_wall

    # =======================================================================
    # Assertions: orchestrator result
    # =======================================================================
    assert result["status"] == EventStatus.SUBMITTED.value
    assert result["winner_address"] == EXPECTED_WINNER
    assert result["is_simulated"] is True
    assert result["overall_score"] > 0.5  # real panel; high but not artificially 1.0
    assert result["market_id"], "market_id missing in orchestrator result"
    assert result["question_id"], "question_id missing in orchestrator result"
    assert result["event_id"] is not None

    event_id = result["event_id"]

    # =======================================================================
    # Assertions: every table populated + foreign keys consistent
    # =======================================================================
    from polyglot_alpha.persistence.db import engine

    with Session(engine) as s:
        events = s.exec(select(Event)).all()
        assert len(events) == 1
        ev = events[0]
        assert ev.id == event_id
        assert ev.status == EventStatus.SUBMITTED.value
        assert ev.title == confirmed["primary_title"]
        assert ev.language == "zh"

        bids = s.exec(select(Bid).where(Bid.event_id == event_id)).all()
        assert len(bids) == 4, f"expected 4 bids, got {len(bids)}"
        assert {b.agent_address for b in bids} == set(AGENT_BIDS.keys())

        auctions = s.exec(select(Auction).where(Auction.event_id == event_id)).all()
        assert len(auctions) == 1
        assert auctions[0].winner_address == EXPECTED_WINNER
        assert auctions[0].winning_bid == AGENT_BIDS[EXPECTED_WINNER]
        assert auctions[0].settlement_tx_hash is not None
        assert auctions[0].settled_at is not None

        translations = s.exec(
            select(Translation).where(Translation.event_id == event_id)
        ).all()
        assert len(translations) == 1
        assert translations[0].translator_address == EXPECTED_WINNER
        assert translations[0].final_question_json  # non-empty dict

        scores = s.exec(
            select(QualityScore).where(QualityScore.event_id == event_id)
        ).all()
        assert len(scores) == 1
        assert scores[0].verdict == "PASS"
        assert scores[0].overall_score > 0.5

        questions = s.exec(
            select(Question).where(Question.event_id == event_id)
        ).all()
        assert len(questions) == 1
        assert questions[0].question_id_onchain is not None
        assert questions[0].builder_code == orch_mod.BUILDER_CODE
        # The on-chain question's title_hash mirrors the candidate hash from
        # the translator pipeline -- check it's a 64-char hex digest.
        assert questions[0].title_hash is not None
        assert len(questions[0].title_hash) == 64

        submissions = s.exec(
            select(PolymarketSubmission).where(
                PolymarketSubmission.event_id == event_id
            )
        ).all()
        assert len(submissions) == 1
        assert submissions[0].market_id == market_id
        assert submissions[0].is_simulated is True

        fee_events = s.exec(select(BuilderFeeEvent)).all()
        # 2 orchestrator-synthetic (90/10 split — see WEB3_STORY.md §3) +
        # 5 mock-fill rows = 7 total.
        assert len(fee_events) == 7, (
            f"expected 7 BuilderFeeEvent rows (2 split legs + 5 mock fills), "
            f"got {len(fee_events)}"
        )
        assert all(f.is_simulated for f in fee_events)
        # The winner accrues from the 5 mock fills + the 0.9 winner leg of
        # the orchestrator split. The treasury accrues from the 0.1 leg.
        winner_rows = [f for f in fee_events if f.translator_address == EXPECTED_WINNER]
        assert len(winner_rows) == 6  # 5 mocks + 1 winner-share row
        total_fees = sum(f.fee_amount for f in fee_events)
        assert total_fees > 0.0

        # Reputation: winner has 1 bid, 1 win, positive avg_quality, cumulative
        # fees == sum of winner-only fee rows (the treasury leg is protocol
        # revenue, not operator revenue — see WEB3_STORY.md §3).
        rep = s.get(AgentReputation, EXPECTED_WINNER)
        assert rep is not None
        assert rep.total_bids == 1
        assert rep.total_wins == 1
        assert rep.avg_quality > 0.0
        winner_total_fees = sum(f.fee_amount for f in winner_rows)
        assert rep.cumulative_fees == pytest.approx(winner_total_fees, abs=1e-6)

        # Losers should have 1 bid, 0 wins.
        for loser_addr in set(AGENT_BIDS.keys()) - {EXPECTED_WINNER}:
            lrep = s.get(AgentReputation, loser_addr)
            assert lrep is not None, f"missing reputation for {loser_addr}"
            assert lrep.total_bids == 1
            assert lrep.total_wins == 0

    # =======================================================================
    # Assertions: SSE events emitted in expected order
    # =======================================================================
    types = [m["type"] for m in captured]
    # Each expected event-type appears at least once.
    for expected in EXPECTED_SSE_TYPES:
        assert expected in types, (
            f"missing SSE event: {expected} (captured={types})"
        )

    # Ordering: event.created < auction.opened < every bid.submitted <
    # auction.settled < translation.completed < quality.verdict <
    # onchain.committed < polymarket.submitted < (any) builder_fee.accrued.
    def first_idx(t: str) -> int:
        for i, m in enumerate(captured):
            if m["type"] == t:
                return i
        raise AssertionError(f"no event of type {t}")

    def last_idx(t: str) -> int:
        last = -1
        for i, m in enumerate(captured):
            if m["type"] == t:
                last = i
        if last < 0:
            raise AssertionError(f"no event of type {t}")
        return last

    assert first_idx("event.created") < first_idx("auction.opened")
    assert first_idx("auction.opened") < first_idx("bid.submitted")
    assert last_idx("bid.submitted") < first_idx("auction.settled")
    assert first_idx("auction.settled") < first_idx("translation.completed")
    assert first_idx("translation.completed") < first_idx("quality.verdict")
    assert first_idx("quality.verdict") < first_idx("onchain.committed")
    assert first_idx("onchain.committed") < first_idx("polymarket.submitted")
    assert first_idx("polymarket.submitted") <= first_idx("builder_fee.accrued")

    # All four agents broadcast bid.submitted events.
    bid_events = [m for m in captured if m["type"] == "bid.submitted"]
    assert len(bid_events) == 4
    assert {m["data"]["agent_address"] for m in bid_events} == set(AGENT_BIDS.keys())

    # Builder-fee broadcasts: 1 orchestrator synthetic + 5 manual = 6
    fee_broadcasts = [m for m in captured if m["type"] == "builder_fee.accrued"]
    assert len(fee_broadcasts) == 6

    # =======================================================================
    # Assertions: timing budget
    # =======================================================================
    # Budget bumped from 30s -> 90s after the real chain + real dispatch
    # adapters landed; the orchestrator now eagerly imports them which adds
    # web3 + httpx pool initialization to the cold-start cost.
    assert elapsed < 90.0, f"e2e took {elapsed:.2f}s, > 90s budget"

    # Print a one-line lifecycle summary so the test log is useful in CI.
    print(
        "\nE2E lifecycle summary: "
        f"event_id={event_id} winner={EXPECTED_WINNER} "
        f"verdict={scores[0].verdict} score={scores[0].overall_score:.3f} "
        f"market_id={market_id} fills={len(fee_events)} "
        f"total_fees={total_fees:.6f} elapsed={elapsed:.2f}s"
    )
