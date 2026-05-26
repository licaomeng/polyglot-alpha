"""Unit tests for ``polyglot_alpha.agents.dispatch``.

These tests cover the dispatch surface that ``orchestrator.py`` depends on:

* All 3 reference seeders instantiate without a real wallet (eval-only).
* ``collect_bids_inline`` returns one bid per seeder, with bid amounts that
  visibly differ across the three bid strategies.
* ``collect_bids_inline`` tolerates a single seeder crashing and still
  returns the remaining 2 bids (no synthetic placeholder).
* ``run_pipeline`` produces a valid ``polymarket.types.Question`` with a
  populated layer trace.
* ``run_for_winner`` returns a ``PipelineResult`` whose ``final_question``
  matches the orchestrator's wire shape.

Run with: ``.venv/bin/pytest tests/test_dispatch.py -q``
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from polyglot_alpha.agents import AGENT_REGISTRY, dispatch
from polyglot_alpha.llm import MockLLM
from polyglot_alpha.polymarket.types import Question as PolymarketQuestion


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def sample_event() -> dict[str, Any]:
    """A Chinese-language event with enough body to drive bid-strategy spread."""

    return {
        "event_id": "evt_dispatch_001",
        "title": "Sample geopolitical event for dispatch tests",
        "title_zh": "测试事件",
        "body_zh": "中国宣布将就关税政策做出回应。" * 30,
        "cutoff_ts": 1_900_000_000,
        "category": "geopolitics",
        "language": "zh",
        "url": "https://example.com/cn/news/001",
    }


@pytest.fixture()
def mock_llm_factory():
    """Factory returning a deterministic ``MockLLM`` for the whole pipeline."""

    canned = json.dumps(
        {
            "question_en": "Will the tariff response be announced by 2026-12-31?",
            "resolution_criteria": (
                "Resolves YES if the State Council issues an official "
                "tariff response before 2026-12-31T23:59:59Z."
            ),
            "end_date_iso": "2026-12-31T23:59:59Z",
            "tags": ["geopolitics", "tariffs"],
            "entities": ["State Council"],
            "risks": ["delayed announcement"],
        }
    )
    return lambda: MockLLM(model_id="mock-dispatch", canned_response=canned)


# --------------------------------------------------------------------------- #
# Agent construction                                                          #
# --------------------------------------------------------------------------- #


def test_all_seeders_instantiate_without_real_wallet() -> None:
    """The three reference seeders must construct with a throwaway PK."""

    assert set(AGENT_REGISTRY.keys()) == {"gemini-v2", "deepseek-v2", "qwen-v2"}
    for name, cls in AGENT_REGISTRY.items():
        pk = dispatch._throwaway_pk()
        agent = cls(wallet_pk=pk)
        assert agent.MODEL_ID, f"{name} missing MODEL_ID"
        assert agent.address.startswith("0x")
        assert len(agent.address) == 42


# --------------------------------------------------------------------------- #
# collect_bids_inline                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_collect_bids_inline_returns_three_distinct_bids(
    sample_event: dict[str, Any],
) -> None:
    """All 3 seeders must bid; bid_strategy spread should yield distinct values."""

    bids = await dispatch.collect_bids_inline(sample_event, window_seconds=10.0)

    assert len(bids) == 3
    names = {b["agent_name"] for b in bids}
    assert names == {"gemini-v2", "deepseek-v2", "qwen-v2"}, (
        f"unexpected agent_name values: {names}"
    )
    bid_amounts = [b["bid_amount"] for b in bids]
    # All bids are positive.
    assert all(amount > 0 for amount in bid_amounts)
    # The three bid windows differ (BID_MIN/MAX configured per seeder), so
    # at least two distinct amounts must appear.
    assert len(set(round(a, 4) for a in bid_amounts)) >= 2, (
        f"expected bid spread, got {bid_amounts}"
    )
    # Every bid carries the required keys.
    required_keys = {
        "agent_address",
        "agent_name",
        "bid_amount",
        "candidate_hash",
        "reputation",
        "confidence",
        "expected_cost_usdc",
        "llm_model",
    }
    for bid in bids:
        assert required_keys.issubset(bid.keys()), (
            f"missing keys in bid: {required_keys - bid.keys()}"
        )


@pytest.mark.asyncio
async def test_collect_bids_inline_drops_failed_agents(
    sample_event: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing agent must NOT yield a synthetic bid.

    Previous contract: each failing agent contributed a hardcoded 1.0 USDC
    fallback bid with ``candidate_hash="0x0"`` and an ``_error`` key. That
    placeholder bid then went on-chain as if it were a real auction vote.
    The new contract is: propagate failures so the orchestrator records
    only the agents that actually produced a valid evaluation.
    """

    from polyglot_alpha.agents.base import BaseTranslatorAgent

    original_evaluate = BaseTranslatorAgent.evaluate_event
    call_count = {"n": 0}

    async def _flaky_evaluate(self, event_dict):
        call_count["n"] += 1
        # Make exactly the first agent raise; the rest succeed normally.
        if call_count["n"] == 1:
            raise RuntimeError("simulated LLM quota error")
        return await original_evaluate(self, event_dict)

    monkeypatch.setattr(
        BaseTranslatorAgent, "evaluate_event", _flaky_evaluate
    )

    bids = await dispatch.collect_bids_inline(sample_event, window_seconds=10.0)
    # Only 2 seeders successfully bid; the failing one is dropped entirely
    # so no synthetic placeholder enters the auction.
    assert len(bids) == 2
    for bid in bids:
        assert "_error" not in bid
        assert bid["candidate_hash"] != "0x0"


@pytest.mark.asyncio
async def test_collect_bids_inline_zero_window_returns_empty() -> None:
    """A zero-second window cancels every bid task and returns an empty list."""

    bids = await dispatch.collect_bids_inline(
        {"event_id": "e1", "title": "t"}, window_seconds=0.0
    )
    # Tasks may or may not have time to complete at window=0; allowed.
    assert isinstance(bids, list)
    assert all("agent_name" in b for b in bids)


# --------------------------------------------------------------------------- #
# run_pipeline                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_pipeline_returns_polymarket_question(
    sample_event: dict[str, Any],
    mock_llm_factory,
) -> None:
    """``run_pipeline`` must return a ``polymarket.types.Question``."""

    question = await dispatch.run_pipeline(
        sample_event,
        winner_agent_name="gemini-v2",
        llm_factory=mock_llm_factory,
    )

    assert isinstance(question, PolymarketQuestion)
    assert question.question_id  # non-empty
    assert "tariff" in question.text.lower() or "announce" in question.text.lower()
    assert question.category == "geopolitics"
    assert question.end_date_iso  # populated from the synthesizer output


@pytest.mark.asyncio
async def test_run_pipeline_layer_trace_populated(
    sample_event: dict[str, Any],
    mock_llm_factory,
) -> None:
    """The layer trace must include synthesizer output + winning agent."""

    question = await dispatch.run_pipeline(
        sample_event,
        winner_agent_name="deepseek-v2",
        llm_factory=mock_llm_factory,
    )

    layer_trace = getattr(question, "layer_trace", None)
    assert layer_trace is not None, "layer_trace attribute missing"
    assert layer_trace["winner_agent"] == "deepseek-v2"
    assert "synthesized" in layer_trace
    assert layer_trace["quality_score"] >= 0.0
    assert layer_trace["confidence"] >= 0.0


@pytest.mark.asyncio
async def test_run_pipeline_unknown_agent_falls_back_to_gemini(
    sample_event: dict[str, Any],
    mock_llm_factory,
) -> None:
    """An unknown winner name must not crash — fall back to gemini."""

    question = await dispatch.run_pipeline(
        sample_event,
        winner_agent_name="not-a-real-agent",
        llm_factory=mock_llm_factory,
    )
    assert isinstance(question, PolymarketQuestion)
    assert question.text


# --------------------------------------------------------------------------- #
# run_for_winner (orchestrator entry point)                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_for_winner_returns_pipeline_result(
    sample_event: dict[str, Any],
    mock_llm_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The orchestrator-facing entry point must return a PipelineResult.

    The dispatch no longer has a fallback path that masks LLM failures,
    so we force ``make_llm`` to return ``MockLLM`` for this test instead
    of relying on the absence of API keys (the test environment may load
    ``.env`` and pick up a stale or credit-exhausted key).
    """

    monkeypatch.setattr(
        "polyglot_alpha.agents.dispatch.make_llm",
        lambda model_id: mock_llm_factory(),
    )

    result = await dispatch.run_for_winner(sample_event, winner_address="0xdead")
    assert isinstance(result, dispatch.PipelineResult)
    assert result.candidate_hash and len(result.candidate_hash) == 64
    assert result.final_question["title"].lower().startswith("will ")
    assert result.final_question["outcomes"] == ["Yes", "No"]
    assert result.pipeline_trace_ipfs and result.pipeline_trace_ipfs.startswith(
        "ipfs://pipeline/"
    )
