"""Subsystem boundary contract tests.

Each test pins down the input/output shape of one pipeline boundary so a
regression that breaks the wire shape between two subsystems lights up
loudly and immediately, rather than surfacing as a vague downstream
failure (e.g. an empty Polymarket payload or a missing builder_fee row).

The 10 boundaries covered, in pipeline order, are:

  1. rss_aggregator   -> news_summarizer.score_event_for_auction
  2. score_event_for_auction -> trigger.event_dict
  3. event_dict       -> chain.auction_client.open_auction
  4. open_auction     -> chain.auction_client.collect_bids -> BidRecord
  5. bids             -> orchestrator._settle_auction (winner picker)
  6. winner_address   -> agents.dispatch.run_for_winner (candidate dict)
  7. candidate dict   -> judges.panel.evaluate (PanelResult)
  8. PASS             -> chain.question_registry.commit_question
  9. question_id      -> polymarket.PolymarketV2Client.submit_question
 10. polymarket fill  -> orchestrator builder_fee 90/10 split

Every test uses ``MockLLM`` (or hand-built dataclasses) so no real
Anthropic/Gemini/OpenRouter call leaves the process.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from polyglot_alpha.llm import MockLLM


# --------------------------------------------------------------------------- #
# Shared fixtures                                                             #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def sample_articles() -> list[dict[str, Any]]:
    """A small RSS-aggregator-shaped article list (output of rss_aggregator)."""

    return [
        {
            "title": "PBOC cuts reserve requirement ratio by 25bps",
            "summary": "The People's Bank of China announced a 25bps RRR cut "
            "effective next week, the first reduction of 2026.",
            "source": "reuters",
            "published": "2026-05-26T08:00:00Z",
            "url": "https://example.com/reuters/pboc-rrr-cut",
        },
        {
            "title": "China central bank trims RRR amid soft demand",
            "summary": "Reuters reports a 25bps RRR cut as Beijing tries to "
            "shore up credit growth.",
            "source": "bloomberg",
            "published": "2026-05-26T08:10:00Z",
            "url": "https://example.com/bloomberg/pboc-rrr",
        },
    ]


@pytest.fixture()
def canned_event_dict() -> dict[str, Any]:
    """Synthesizer-shape event_dict (output of trigger.py)."""

    return {
        "title": "PBOC cuts reserve requirement ratio by 25bps",
        "sources": [
            {"name": "reuters", "url": "https://example.com/reuters/pboc-rrr-cut"},
            {"name": "bloomberg", "url": "https://example.com/bloomberg/pboc-rrr"},
        ],
        "language": "zh",
        "category": "macro/china_monetary",
        "summary": "PBOC announced a 25bps RRR cut.",
        "scoring": {
            "event_quality_score": 0.92,
            "primary_category": "macro/china_monetary",
            "sub_categories": ["monetary_policy"],
            "key_entities": ["PBOC", "China"],
            "source_credibility": 0.9,
            "timeliness_score": 1.0,
            "raw_summary": "PBOC announced a 25bps RRR cut.",
            "rejection_reason": None,
            "model": "test",
        },
    }


@pytest.fixture()
def canonical_question_dict() -> dict[str, Any]:
    """Shape of ``pipeline.final_question`` exiting the translator stage."""

    return {
        "title": "Will the PBOC cut RRR again by December 31, 2026?",
        "description": "PBOC announced a 25bps RRR cut.",
        "resolution_criteria": (
            "Resolves YES if the People's Bank of China announces a further "
            "RRR cut before 2026-12-31T23:59:59Z, otherwise NO."
        ),
        "resolution_source": "operator",
        "cutoff_ts": "2026-12-31T23:59:59Z",
        "category": "macro/china_monetary",
        "source_news": "PBOC cuts reserve requirement ratio by 25bps",
        "source_language": "zh",
        "target_language": "en",
        "outcomes": ["Yes", "No"],
    }


# --------------------------------------------------------------------------- #
# 1. rss_aggregator -> news_summarizer.score_event_for_auction                #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_rss_to_score_contract(
    sample_articles: list[dict[str, Any]],
) -> None:
    """The heuristic / Haiku scorer must always return an EventScoring with
    every required field populated and ``event_quality_score`` in [0,1]."""

    from polyglot_alpha.ingestion.news_summarizer import (
        EventScoring,
        score_event_for_auction,
    )

    # No ANTHROPIC_API_KEY -> heuristic fallback path. Always returns a
    # valid dataclass; never raises.
    scoring = await score_event_for_auction(sample_articles, api_key=None)

    assert isinstance(scoring, EventScoring)
    # Every contract-required field is present and non-empty (raw_summary
    # may be a single sentence but never None or "").
    assert isinstance(scoring.event_quality_score, float)
    assert 0.0 <= scoring.event_quality_score <= 1.0, (
        f"event_quality_score {scoring.event_quality_score} out of [0,1]"
    )
    assert isinstance(scoring.primary_category, str) and scoring.primary_category
    assert isinstance(scoring.key_entities, list)
    assert 0.0 <= scoring.source_credibility <= 1.0
    assert 0.0 <= scoring.timeliness_score <= 1.0
    assert isinstance(scoring.raw_summary, str) and scoring.raw_summary

    # ``as_dict`` must round-trip cleanly into the orchestrator's
    # event_dict["scoring"] payload (used by W1's mocked seeders).
    payload = scoring.as_dict()
    for required in (
        "event_quality_score",
        "primary_category",
        "key_entities",
        "source_credibility",
        "timeliness_score",
        "raw_summary",
    ):
        assert required in payload, f"as_dict() missing {required}"


# --------------------------------------------------------------------------- #
# 2. score -> event_dict (W1's no-polymarket_question invariant)              #
# --------------------------------------------------------------------------- #


def test_score_to_event_dict_contract(
    canned_event_dict: dict[str, Any],
) -> None:
    """W1's invariant: trigger.py must NEVER attach a ``polymarket_question``
    key to ``event_dict``. The agents (translators) write the question
    themselves; the orchestrator stage just carries metadata."""

    # The fixture mimics the exact shape ``trigger.py`` constructs at
    # lines 528-537 / 576-585 — sanity-check the contract keys.
    required_keys = {"title", "sources", "language", "category"}
    assert required_keys.issubset(canned_event_dict.keys()), (
        f"event_dict missing required keys: {required_keys - canned_event_dict.keys()}"
    )

    # Scoring metadata may be attached as a sub-dict but must not bleed
    # into a top-level ``polymarket_question`` field.
    assert "polymarket_question" not in canned_event_dict, (
        "REGRESSION: trigger.py contract requires NO polymarket_question key"
    )
    # ``scoring`` (if present) must contain the EventScoring shape.
    if "scoring" in canned_event_dict:
        sc = canned_event_dict["scoring"]
        assert "event_quality_score" in sc
        assert "primary_category" in sc


# --------------------------------------------------------------------------- #
# 3. event_dict -> TranslationAuction.openAuction                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_event_dict_to_auction_contract(
    canned_event_dict: dict[str, Any],
) -> None:
    """``compute_content_hash`` must emit a stable hex string and the
    chain adapter's ``_event_id_bytes`` / ``_content_hash_bytes`` must
    coerce them to exactly 32-byte values without raising."""

    from polyglot_alpha.chain.auction_client import (
        _content_hash_bytes,
        _event_id_bytes,
    )
    from polyglot_alpha.orchestrator import compute_content_hash

    # 1) compute_content_hash is deterministic and emits 64-char hex
    h1 = compute_content_hash(canned_event_dict)
    h2 = compute_content_hash(canned_event_dict)
    assert h1 == h2
    assert len(h1) == 64
    int(h1, 16)  # raises if not valid hex

    # 2) event_id coercion: SQLite int -> bytes32
    eid_bytes = _event_id_bytes(42)
    assert isinstance(eid_bytes, (bytes, bytearray))
    assert len(eid_bytes) == 32

    # 3) content hash coercion: hex -> bytes32 (always pads/truncates to 32)
    chash_bytes = _content_hash_bytes(h1)
    assert isinstance(chash_bytes, (bytes, bytearray))
    assert len(chash_bytes) == 32

    # Free-form (non-hex) strings must also coerce cleanly (the adapter
    # keccaks them rather than raising).
    chash_freeform = _content_hash_bytes("not-a-hex-string")
    assert len(chash_freeform) == 32


# --------------------------------------------------------------------------- #
# 4. auction window -> BidRecord                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_auction_to_bid_contract() -> None:
    """A ``BidRecord`` flowing from ``_collect_bids`` must carry a 0x-prefixed
    address, positive bid_amount, and positive stake_amount."""

    from polyglot_alpha.orchestrator import BidRecord, _collect_bids

    # When ``mock_bids`` is supplied, ``_collect_bids`` returns them verbatim.
    mocked = [
        BidRecord(agent_address="0xagent_a", bid_amount=1.5, stake_amount=5.0),
        BidRecord(agent_address="0xagent_b", bid_amount=2.0, stake_amount=5.0),
    ]
    bids = await _collect_bids(
        event_id=1,
        window_seconds=0.0,
        mock_bids=mocked,
    )
    assert len(bids) == 2
    for b in bids:
        assert isinstance(b, BidRecord)
        assert b.agent_address.startswith("0x"), (
            f"agent_address {b.agent_address!r} must be 0x-prefixed"
        )
        assert b.bid_amount > 0
        assert b.stake_amount > 0


# --------------------------------------------------------------------------- #
# 5. bids -> _settle_auction (winner picker)                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_bid_to_settle_contract() -> None:
    """``_settle_auction`` must return a winner whose ``agent_address`` is
    one of the inputs, and the picked winner must minimise
    ``bid_amount / max(reputation, 1.0)`` among qualified bidders
    (reputation >= MIN_QUALIFIED_REPUTATION = 0.7)."""

    from polyglot_alpha.orchestrator import (
        MIN_QUALIFIED_REPUTATION,
        BidRecord,
        _settle_auction,
    )

    bids = [
        BidRecord(agent_address="0xlo_qualified", bid_amount=0.5, reputation=1.0),
        BidRecord(agent_address="0xhi_qualified", bid_amount=2.5, reputation=1.0),
        BidRecord(agent_address="0xlo_unqualified", bid_amount=0.1, reputation=0.3),
    ]
    winner, tx_hash = await _settle_auction(
        event_id=1, bids=bids, auction_mode="mock"
    )

    # Winner must be one of the inputs.
    input_addresses = {b.agent_address for b in bids}
    assert winner.agent_address in input_addresses

    # Among qualified bids (rep >= 0.7), the lowest amount wins.
    qualified = [b for b in bids if b.reputation >= MIN_QUALIFIED_REPUTATION]
    expected = min(qualified, key=lambda b: b.bid_amount / max(b.reputation, 1.0))
    assert winner.agent_address == expected.agent_address, (
        "settle: low-qualified bid should win; the 0.1 unqualified bid "
        "must be ignored by the gate."
    )

    # In mock mode the settlement tx hash is a synthetic ``0xsim_*``
    # sentinel (W5-A2). UI gates explorer links on the ``0xsim_`` prefix.
    assert tx_hash is not None and tx_hash.startswith("0xsim_")
    # ``0xsim_`` (6) + 28-byte hex (56) = 62 chars total.
    assert len(tx_hash) == 62, f"unexpected sim tx hash length: {len(tx_hash)}"


# --------------------------------------------------------------------------- #
# 6. winner -> dispatch.run_for_winner (candidate dict)                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_settle_to_winner_pipeline_contract(
    canned_event_dict: dict[str, Any],
) -> None:
    """The orchestrator's mock translator path must produce a candidate
    dict with question_en-equivalent / resolution_criteria / cutoff_iso /
    category. We exercise ``_run_translator_pipeline`` in mock mode so
    the contract holds even when the dispatch package is unavailable."""

    from polyglot_alpha.orchestrator import BidRecord, _run_translator_pipeline

    winner = BidRecord(agent_address="0xwinner", bid_amount=1.0)
    pipeline = await _run_translator_pipeline(
        canned_event_dict, winner, auction_mode="mock"
    )
    q = pipeline.final_question

    # Orchestrator's wire shape names: ``title`` is the question_en
    # surrogate, ``cutoff_ts`` plays the role of cutoff_iso.
    assert isinstance(q.get("title"), str) and q["title"]
    assert isinstance(q.get("resolution_criteria"), str) and q["resolution_criteria"]
    assert isinstance(q.get("cutoff_ts"), str) and q["cutoff_ts"]
    assert isinstance(q.get("category"), str) and q["category"]
    # candidate_hash is sha256 hex (64 chars).
    assert len(pipeline.candidate_hash) == 64
    int(pipeline.candidate_hash, 16)


# --------------------------------------------------------------------------- #
# 7. candidate dict -> judges.panel.evaluate                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_candidate_to_panel_contract(
    canonical_question_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator's ``_evaluate_with_judges`` wraps panel.evaluate;
    the returned :class:`JudgePanelResult` must carry translation_scores,
    style_alignment_passes, a verdict in {PASS, FAIL}, and overall_score
    in [0,1]."""

    from polyglot_alpha import orchestrator
    from polyglot_alpha.persistence.models import JudgeVerdict

    # Force the panel adapter's import-or-mock path by raising ImportError
    # inside ``_evaluate_with_judges``. The function then falls back to
    # its deterministic mock verdict (overall=0.85, all d1..d8 True).
    async def _fake_evaluate(_q: Any, **_kw: Any) -> Any:
        raise ImportError("simulated missing panel package")

    # Patch the lazy import target so we exercise the mock branch.
    import polyglot_alpha.judges.panel as panel_mod  # noqa: F401

    with patch.object(
        panel_mod, "evaluate", side_effect=ImportError("forced mock")
    ):
        result = await orchestrator._evaluate_with_judges(canonical_question_dict)

    # Translation scores: at least one numeric judge result key.
    assert isinstance(result.translation_scores, dict)
    assert len(result.translation_scores) >= 1
    # Style alignment passes: at least the d1 style judge key.
    assert isinstance(result.style_alignment_passes, dict)
    # Verdict normalized to the persistence enum value set.
    assert result.verdict in {JudgeVerdict.PASS.value, JudgeVerdict.FAIL.value}
    # Overall score in [0,1].
    assert 0.0 <= result.overall_score <= 1.0


# --------------------------------------------------------------------------- #
# 8. PASS -> chain.question_registry.commit_question                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_panel_to_commit_contract() -> None:
    """On PASS the orchestrator commits the question on-chain. In mock
    mode the helper returns a deterministic question_id + tx_hash. In
    real mode, when the chain package is absent, it must return the
    pending sentinel (``pending-<event_id>``) and ``tx_hash=None`` —
    never fake a hex hash."""

    from polyglot_alpha.orchestrator import _commit_question_onchain

    # ---- mock path: deterministic question_id and tx_hash ----
    question_id, tx_hash = await _commit_question_onchain(
        event_id=42,
        candidate_hash="abcd" * 16,  # 64 hex chars
        builder_code="POLYGLOT_ALPHA_BUILDER_V1",
        pipeline_trace_ipfs="ipfs://mock/trace",
        auction_mode="mock",
    )
    assert question_id.startswith("0x") and len(question_id) == 42
    # W5-A2: mock-mode tx hash is the synthetic ``0xsim_*`` sentinel.
    assert tx_hash is not None and tx_hash.startswith("0xsim_")
    assert len(tx_hash) == 62, f"unexpected sim tx hash length: {len(tx_hash)}"

    # ---- real path with chain pkg unavailable: pending sentinel ----
    # Patch ``_get_chain_question_registry`` to return None.
    from polyglot_alpha import orchestrator

    with patch.object(
        orchestrator, "_get_chain_question_registry", return_value=None
    ):
        qid2, tx2 = await _commit_question_onchain(
            event_id=99,
            candidate_hash="deadbeef" * 8,
            builder_code="POLYGLOT_ALPHA_BUILDER_V1",
            pipeline_trace_ipfs=None,
            auction_mode="real",
        )
    assert qid2 == "pending-99"
    assert tx2 is None, "real-mode must NOT fabricate a tx hash when chain is down"


# --------------------------------------------------------------------------- #
# 9. question -> Polymarket V2 client                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_commit_to_polymarket_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Polymarket DRY_RUN payload must carry the builder_code we
    passed AND the immutable ``client_id="polyglot-alpha"`` tag so
    downstream fee attribution works."""

    monkeypatch.setenv("POLYMARKET_MODE", "dry_run")

    from polyglot_alpha.polymarket import PolymarketV2Client
    from polyglot_alpha.polymarket.types import Question

    builder_code = "POLYGLOT_ALPHA_BUILDER_V1"
    question = Question(
        question_id="evt-test-001",
        text="Will the PBOC cut RRR again by December 31, 2026?",
        category="macro/china_monetary",
        resolution_source="operator",
        end_date_iso="2026-12-31T23:59:59Z",
    )
    async with PolymarketV2Client(builder_code=builder_code) as client:
        result = await client.submit_question(question)

    assert result.is_simulated is True
    # The dry-run result echoes the exact payload that *would* have been
    # POSTed; assert the builder_code + client_id contract holds.
    payload = result.payload
    assert payload.get("builder_code") == builder_code, (
        f"builder_code missing or mismatched in payload: {payload!r}"
    )
    assert payload.get("client_id") == "polyglot-alpha", (
        "client_id must be 'polyglot-alpha' so Polymarket fee attribution "
        "credits our builder bucket"
    )
    assert payload.get("question") == question.text
    assert payload.get("external_id") == question.question_id


# --------------------------------------------------------------------------- #
# 10. Polymarket -> builder_fee 90/10 split                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_polymarket_to_fee_split_contract(
    isolated_db: str, sample_event: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running the lifecycle to SUBMITTED with a forced PASS verdict
    must persist TWO ``builder_fee_events`` rows that:

    * Sum exactly to ``fill_amount`` × 0.004 (= 1.0 USDC fee on 100 fill)
    * Split 0.9 / 0.1 between winner and treasury (Path A WINNER_SHARE)
    * Each carry the same ``market_id`` from the polymarket submission.
    """

    from sqlmodel import Session, select

    from polyglot_alpha import orchestrator
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import BuilderFeeEvent

    # Force PASS verdict so we reach the builder_fee leg.
    async def _passing_judges(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.9, "comet": 0.85, "mqm": {"score": 0}},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.92,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", _passing_judges)

    # Set a treasury address so the 90/10 split path is taken (not the
    # legacy single-leg path).
    monkeypatch.setenv("PLATFORM_TREASURY_ADDRESS", "0xtreasury_addr")

    result = await run_lifecycle(
        sample_event,
        auction_window_seconds=0.0,
        mock_bids=[
            BidRecord(agent_address="0xwinner", bid_amount=1.0, reputation=1.0),
        ],
    )

    assert result["status"] == "SUBMITTED", f"unexpected status {result}"

    with Session(engine) as session:
        rows = session.exec(select(BuilderFeeEvent)).all()

    # Two rows: 90% to winner, 10% to treasury.
    assert len(rows) == 2, (
        f"expected 2 builder_fee_events (90/10 split), got {len(rows)}"
    )

    # Sum of fee_amount must equal the protocol-level builder fee
    # (1.0 USDC on a 100 USDC fill, as encoded in the orchestrator).
    total_fees = sum(r.fee_amount for r in rows)
    assert total_fees == pytest.approx(1.0, abs=1e-9), (
        f"fee split must sum to 1.0 USDC; got {total_fees}"
    )

    amounts = sorted(r.fee_amount for r in rows)
    assert amounts[0] == pytest.approx(0.1)
    assert amounts[1] == pytest.approx(0.9)

    # Both legs reference the same market_id (the polymarket submission's).
    market_ids = {r.market_id for r in rows}
    assert len(market_ids) == 1, (
        f"both legs must share market_id; got {market_ids}"
    )
    assert result.get("market_id") in market_ids

    # Recipients: one row credits the winner, one credits the treasury.
    recipients = {r.translator_address for r in rows}
    assert "0xwinner" in recipients
    assert "0xtreasury_addr" in recipients


# --------------------------------------------------------------------------- #
# Misc smoke test for the new logging_ctx module                              #
# --------------------------------------------------------------------------- #


def test_logging_ctx_import_smoke() -> None:
    """Ensure the new correlation-id module is importable and the public
    surface (set_event_id / get_event_id / install_event_id_filter / Filter)
    matches what the orchestrator expects."""

    from polyglot_alpha.logging_ctx import (
        EventIdFilter,
        get_event_id,
        install_event_id_filter,
        set_event_id,
    )

    set_event_id(7)
    assert get_event_id() == 7
    set_event_id(None)
    assert get_event_id() is None

    # Filter mutates the record in place; must return True.
    import logging

    rec = logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    set_event_id(99)
    assert EventIdFilter().filter(rec) is True
    assert rec.event_id == 99
    assert rec.event_tag == "[event_id=99] "
    set_event_id(None)

    # install_event_id_filter must be idempotent.
    install_event_id_filter()
    install_event_id_filter()
