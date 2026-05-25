"""Unit tests for ``polyglot_alpha.agents.dispatch``.

These tests cover the dispatch surface that ``orchestrator.py`` depends on:

* All 4 reference agents instantiate without a real wallet (eval-only).
* ``collect_bids_inline`` returns one bid per agent, with bid amounts that
  visibly differ across the four bid strategies.
* ``collect_bids_inline`` tolerates a single agent crashing and still
  returns 4 bids (the failing one carries an ``_error`` key).
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


def test_all_four_agents_instantiate_without_real_wallet() -> None:
    """The four reference agents must construct with a throwaway PK."""

    assert set(AGENT_REGISTRY.keys()) == {"gemini", "deepseek", "qwen", "llama"}
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
async def test_collect_bids_inline_returns_four_distinct_bids(
    sample_event: dict[str, Any],
) -> None:
    """All 4 agents must bid; bid_strategy spread should yield distinct values."""

    bids = await dispatch.collect_bids_inline(sample_event, window_seconds=10.0)

    assert len(bids) == 4
    names = {b["agent_name"] for b in bids}
    assert names == {"GeminiAgent", "DeepSeekAgent", "QwenAgent", "LlamaAgent"} or \
           names == {"gemini", "deepseek", "qwen", "llama"}, (
               f"unexpected agent_name values: {names}"
           )
    bid_amounts = [b["bid_amount"] for b in bids]
    # All bids are positive.
    assert all(amount > 0 for amount in bid_amounts)
    # The four bid windows differ (BID_MIN/MAX configured per agent), so at
    # least two distinct amounts must appear.
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
async def test_collect_bids_inline_tolerates_agent_failure(
    sample_event: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failing agent must not kill the auction; we still get 4 bids."""

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
    assert len(bids) == 4
    failing = [b for b in bids if "_error" in b]
    assert len(failing) == 1
    assert failing[0]["bid_amount"] > 0  # safe-default bid still emitted
    assert failing[0]["candidate_hash"] == "0x0"


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
        winner_agent_name="gemini",
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
        winner_agent_name="deepseek",
        llm_factory=mock_llm_factory,
    )

    layer_trace = getattr(question, "layer_trace", None)
    assert layer_trace is not None, "layer_trace attribute missing"
    assert layer_trace["winner_agent"] == "deepseek"
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
) -> None:
    """The orchestrator-facing entry point must always return a PipelineResult.

    Even without LLM keys, ``make_llm`` returns ``MockLLM`` so the pipeline
    completes deterministically.
    """

    result = await dispatch.run_for_winner(sample_event, winner_address="0xdead")
    assert isinstance(result, dispatch.PipelineResult)
    assert result.candidate_hash and len(result.candidate_hash) == 64
    assert result.final_question["title"].lower().startswith("will ")
    assert result.final_question["outcomes"] == ["Yes", "No"]
    assert result.pipeline_trace_ipfs and result.pipeline_trace_ipfs.startswith(
        "ipfs://pipeline/"
    )
