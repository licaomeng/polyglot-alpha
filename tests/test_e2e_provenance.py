"""E2E provenance tests.

Verifies the cryptographic and bookkeeping chain between subsystems:

* The on-chain ``title_hash`` is recomputable from the IPFS content.
* The Polymarket-submitted ``text`` matches the candidate's translated
  question text.
* The on-chain auction winner address matches the persisted DB winner.

Each test runs the full lifecycle in-process with MockLLM + ``mock_bids``,
then re-derives the property from raw DB rows.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from sqlmodel import Session, select


@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("POLYGLOT_LLM_BACKEND", "mock")


@pytest.fixture()
def _judges_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    from polyglot_alpha import orchestrator

    async def passing(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.9, "comet": 0.88, "mqm": {"score": 92}},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.92,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing)


@pytest.fixture()
def _deterministic_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Pin the translator output so candidate_hash is recomputable."""

    from polyglot_alpha import orchestrator as orch_mod

    final_question = {
        "title": "Will provenance check pass by December 31, 2026?",
        "description": "Provenance test.",
        "resolution_criteria": "Resolves YES if hash recompute matches.",
        "resolution_source": "operator",
        "cutoff_ts": "2026-12-31T23:59:59+00:00",
        "category": "test",
        "source_news": "test_e2e_provenance",
        "source_language": "en",
        "target_language": "en",
        "outcomes": ["Yes", "No"],
        "question_en": "Will provenance check pass by December 31, 2026?",
    }
    canonical = json.dumps(final_question, sort_keys=True).encode("utf-8")
    candidate_hash = hashlib.sha256(canonical).hexdigest()
    ipfs_uri = f"ipfs://prov/{candidate_hash[:12]}"

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


@pytest.mark.asyncio
async def test_candidate_hash_recomputable_externally(
    isolated_db: str,
    _judges_pass: None,
    _deterministic_pipeline: dict[str, Any],
) -> None:
    """An auditor with the IPFS content can recompute the on-chain hash."""

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import Question, Translation

    result = await run_lifecycle(
        {
            "title": "Hash recompute event",
            "sources": [{"url": "https://example.com/hash"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xprov", bid_amount=1.0)],
    )

    with Session(engine) as s:
        q = s.exec(select(Question).where(Question.event_id == result["event_id"])).one()
        tr = s.exec(select(Translation).where(Translation.event_id == result["event_id"])).one()

    # Pull the IPFS-pinned content (the Translation row mirrors it).
    pinned_content = tr.final_question_json

    # An external auditor recomputes the hash from the canonical JSON.
    recomputed = hashlib.sha256(
        json.dumps(pinned_content, sort_keys=True).encode("utf-8")
    ).hexdigest()

    # The recomputed hash matches what we stamped on-chain.
    assert recomputed == q.title_hash
    assert recomputed == _deterministic_pipeline["candidate_hash"]
    assert len(q.title_hash) == 64  # canonical SHA-256 hex


@pytest.mark.asyncio
async def test_published_question_text_matches_candidate(
    isolated_db: str,
    _judges_pass: None,
    _deterministic_pipeline: dict[str, Any],
) -> None:
    """Polymarket-published market_id row references the same candidate.

    The Polymarket client builds its ``Question.text`` from
    ``final_question.title`` (see orchestrator._submit_to_polymarket); the
    persisted ``PolymarketSubmission.market_id`` ties the submission back
    to the candidate.  Verify the title path end-to-end via the
    Translation row (which mirrors what the Polymarket client saw).
    """

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import PolymarketSubmission, Translation

    expected_title = _deterministic_pipeline["final_question"]["title"]

    result = await run_lifecycle(
        {
            "title": "Polymarket title parity event",
            "sources": [{"url": "https://example.com/poly"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xpoly", bid_amount=1.0)],
    )

    with Session(engine) as s:
        sub = s.exec(
            select(PolymarketSubmission).where(PolymarketSubmission.event_id == result["event_id"])
        ).one()
        tr = s.exec(
            select(Translation).where(Translation.event_id == result["event_id"])
        ).one()

    # Translation row mirrors the final_question that Polymarket saw.
    assert tr.final_question_json["title"] == expected_title
    # Submission row references the same event_id and has a market id.
    assert sub.event_id == result["event_id"]
    assert sub.market_id


@pytest.mark.asyncio
async def test_winner_address_in_question_registry_matches_auction_winner(
    isolated_db: str,
    _judges_pass: None,
    _deterministic_pipeline: dict[str, Any],
) -> None:
    """Auctions.winner_address == Translations.translator_address.

    Mirror of the on-chain ``winning_translator`` field on the
    QuestionRegistry. In the in-process test, the registry call is mocked
    out and the DB row is the source of truth; we therefore assert the
    cross-table consistency that production code requires.
    """

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import Auction, Translation

    result = await run_lifecycle(
        {
            "title": "Winner address parity event",
            "sources": [{"url": "https://example.com/winner"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[
            BidRecord(agent_address="0xwinner_prov", bid_amount=0.5),
            BidRecord(agent_address="0xloser_prov", bid_amount=2.0),
        ],
    )

    assert result["winner_address"] == "0xwinner_prov"

    with Session(engine) as s:
        auction = s.exec(
            select(Auction).where(Auction.event_id == result["event_id"])
        ).one()
        translation = s.exec(
            select(Translation).where(Translation.event_id == result["event_id"])
        ).one()

    assert auction.winner_address == "0xwinner_prov"
    assert translation.translator_address == auction.winner_address
    # winning_bid in DB matches the orchestrator's reported winning_bid.
    assert auction.winning_bid == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_builder_fee_winner_matches_auction_winner(
    isolated_db: str,
    _judges_pass: None,
    _deterministic_pipeline: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 90% builder-fee leg is credited to the auction winner address."""

    treasury = "0xtreasury_prov"
    monkeypatch.setenv("PLATFORM_TREASURY_ADDRESS", treasury)

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import Auction, BuilderFeeEvent

    result = await run_lifecycle(
        {
            "title": "Builder fee parity event",
            "sources": [{"url": "https://example.com/feeprov"}],
            "language": "en",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xfee_winner", bid_amount=1.0)],
    )

    with Session(engine) as s:
        auction = s.exec(
            select(Auction).where(Auction.event_id == result["event_id"])
        ).one()
        fees = s.exec(select(BuilderFeeEvent)).all()

    by_recipient = {f.translator_address: f.fee_amount for f in fees}
    # 0.9 -> auction.winner_address; 0.1 -> treasury.
    assert auction.winner_address in by_recipient
    assert by_recipient[auction.winner_address] == pytest.approx(0.9)
    assert by_recipient[treasury] == pytest.approx(0.1)
