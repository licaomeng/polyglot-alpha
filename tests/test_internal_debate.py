"""Unit tests for the internal-debate orchestrator.

All tests stay offline: LLM callables are replaced with ``AsyncMock`` or
plain async stubs so no network / OPENROUTER_API_KEY is required.

Run with: ``.venv/bin/pytest -xvs tests/test_internal_debate.py``
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from polyglot_alpha.agents.critics import CRITIC_MODEL_A, CRITIC_MODEL_B
from polyglot_alpha.agents.internal_debate import (
    InternalDebateResult,
    run_internal_debate,
)
from polyglot_alpha.agents.moderator import MODERATOR_MODEL


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def event() -> Dict[str, Any]:
    return {
        "event_id": "0xdebate_smoke",
        "title_zh": "美联储宣布加息25个基点",
        "body_zh": "美联储于2026年5月议息会议宣布加息25个基点。",
        "url": "https://example.com/fed",
        "cutoff_ts": 1735689600,
    }


@pytest.fixture()
def two_candidates() -> List[Dict[str, Any]]:
    """Two translator candidates; candidate 0 is intentionally stronger."""

    return [
        {
            "translator_id": "t0",
            "title": "Will the Fed raise rates by 25bps at the June 2026 FOMC meeting?",
            "question_en": "Will the Fed raise rates by 25bps at the June 2026 FOMC meeting?",
            "category": "macro",
            "resolution_criteria": (
                "Resolves YES if the Federal Reserve's June 2026 FOMC "
                "statement raises the federal funds target range by "
                "exactly 25 basis points, as published on federalreserve.gov "
                "by 2026-06-30T23:59:59Z."
            ),
            "resolution_source": "federalreserve.gov",
            "end_date_iso": "2026-06-30T23:59:59Z",
            "tags": ["macro", "fed", "rates"],
            "meta": {"model": "deepseek/deepseek-chat"},
        },
        {
            "translator_id": "t1",
            "title": "Will the Fed hike soon?",
            "question_en": "Will the Fed hike soon?",
            "category": "macro",
            "resolution_criteria": "Resolves YES if the Fed hikes.",
            "resolution_source": "",
            "end_date_iso": "2026-12-31T23:59:59Z",
            "tags": ["fed"],
            "meta": {"model": "qwen/qwen-2.5-72b-instruct"},
        },
    ]


def _critic_json(
    issues: List[str], strengths: List[str], verdict: str, confidence: float
) -> str:
    return json.dumps(
        {
            "issues": issues,
            "strengths": strengths,
            "verdict": verdict,
            "confidence": confidence,
        }
    )


def _moderator_json(
    winning_index: int,
    reasoning: List[str],
    confidence: float,
    critique_signal: str,
) -> str:
    return json.dumps(
        {
            "winning_index": winning_index,
            "reasoning": reasoning,
            "confidence": confidence,
            "critique_signal": critique_signal,
        }
    )


def _hash_candidate(candidate: Dict[str, Any]) -> str:
    encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_internal_debate_full_flow_mock_llms(
    event: Dict[str, Any], two_candidates: List[Dict[str, Any]]
) -> None:
    """All 3 stages fire: critic round, moderator, refine — mock LLMs only."""

    async def proposer(_event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Deep-copy via json roundtrip to mimic the real translator stage,
        # which produces fresh dicts on each call.
        return json.loads(json.dumps(two_candidates))

    critic_a = AsyncMock(
        return_value=_critic_json(
            issues=["'soon' is vague", "no source", "drift"],
            strengths=["concise"],
            verdict="needs_refinement",
            confidence=0.84,
        )
    )
    critic_b = AsyncMock(
        return_value=_critic_json(
            issues=["minor: monetary-policy tag"],
            strengths=["precise threshold", "explicit source"],
            verdict="accept_as_is",
            confidence=0.91,
        )
    )

    def critic_factory(model_id: str):
        if model_id == CRITIC_MODEL_A:
            return critic_a
        if model_id == CRITIC_MODEL_B:
            return critic_b
        raise AssertionError(f"unexpected critic model: {model_id}")

    moderator_response = _moderator_json(
        winning_index=0,
        reasoning=[
            "candidate 0 has explicit federalreserve.gov source",
            "candidate 0 specifies exact 25bps threshold",
            "candidate 1 has unmeasurable 'soon'",
        ],
        confidence=0.9,
        critique_signal=(
            "Add monetary-policy tag and clarify upper-bound interpretation."
        ),
    )
    moderator_mock = AsyncMock(return_value=moderator_response)

    def mod_factory(_mid: str):
        return moderator_mock

    refined_payload = {
        "title": "Will the Fed raise rates by 25bps at the June 2026 FOMC meeting?",
        "question_en": (
            "Will the Fed raise the federal funds upper-bound rate by 25bps "
            "at the June 2026 FOMC meeting?"
        ),
        "category": "macro",
        "resolution_criteria": (
            "Resolves YES if the Federal Reserve's June 2026 FOMC statement, "
            "as published on federalreserve.gov by 2026-06-30T23:59:59Z, raises "
            "the federal funds target range upper bound by exactly 25 bps."
        ),
        "resolution_source": "federalreserve.gov",
        "end_date_iso": "2026-06-30T23:59:59Z",
        "tags": ["macro", "fed", "rates", "monetary-policy"],
    }
    refine_mock = AsyncMock(return_value=json.dumps(refined_payload))

    result = await run_internal_debate(
        event,
        propose_candidates_fn=proposer,
        critic_llm_factory=critic_factory,
        moderator_llm_factory=mod_factory,
        refine_llm=refine_mock,
    )

    assert isinstance(result, InternalDebateResult)
    # All three stages fired exactly once each (modulo 2x critic in parallel).
    assert critic_a.await_count == 1
    assert critic_b.await_count == 1
    assert moderator_mock.await_count == 1
    assert refine_mock.await_count == 1

    # Moderator picked candidate 0; refine added the monetary-policy tag.
    assert result.moderator_verdict.winning_index == 0
    assert "monetary-policy" in result.final_candidate["tags"]
    # Refine result body is reflected in final_candidate.
    assert result.final_candidate["question_en"].startswith(
        "Will the Fed raise the federal funds upper-bound rate"
    )

    # Intermediate trace is preserved for auditing.
    assert len(result.intermediate_candidates) == 2
    assert result.intermediate_candidates[0]["translator_id"] == "t0"
    assert len(result.critiques) == 2
    assert {c.verdict for c in result.critiques} == {
        "needs_refinement",
        "accept_as_is",
    }

    # Duration + LLM call counters populated.
    assert result.total_duration_ms >= 0
    # 2 proposer "calls" (one per candidate) + 2 critics + 1 moderator + 1 refine
    assert result.total_llm_calls == 6


@pytest.mark.asyncio
async def test_internal_debate_handles_critic_timeout(
    event: Dict[str, Any], two_candidates: List[Dict[str, Any]]
) -> None:
    """A hanging critic must soft-fail without sinking the debate."""

    async def proposer(_e: Dict[str, Any]) -> List[Dict[str, Any]]:
        return json.loads(json.dumps(two_candidates))

    # CRITIC_MODEL_A hangs forever; CRITIC_MODEL_B returns instantly.
    async def slow_llm(_prompt: str) -> str:
        await asyncio.sleep(5.0)
        return _critic_json([], [], "reject", 1.0)

    fast_mock = AsyncMock(
        return_value=_critic_json(
            ["minor"], ["clear"], "accept_as_is", 0.7
        )
    )

    def critic_factory(model_id: str):
        if model_id == CRITIC_MODEL_A:
            return slow_llm
        return fast_mock

    # Moderator always picks candidate 0 here.
    moderator_mock = AsyncMock(
        return_value=_moderator_json(
            winning_index=0,
            reasoning=["A", "B", "C"],
            confidence=0.8,
            critique_signal="ok",
        )
    )

    result = await run_internal_debate(
        event,
        propose_candidates_fn=proposer,
        critic_llm_factory=critic_factory,
        moderator_llm_factory=lambda _mid: moderator_mock,
        refine_llm=AsyncMock(return_value="{}"),  # refine no-op
        critic_timeout=0.05,
    )

    # The hanging critic soft-failed; the other came back normally.
    soft_failed = [c for c in result.critiques if c.critic_model == CRITIC_MODEL_A]
    assert len(soft_failed) == 1
    assert soft_failed[0].verdict == "accept_as_is"  # the soft-fail default
    assert "soft-fail" in soft_failed[0].raw_response

    # Pipeline still produced a final candidate (moderator chose 0).
    assert result.final_candidate["translator_id"] == "t0"
    assert result.moderator_verdict.winning_index == 0


@pytest.mark.asyncio
async def test_internal_debate_refine_no_op_preserves_candidate(
    event: Dict[str, Any], two_candidates: List[Dict[str, Any]]
) -> None:
    """Malformed refine JSON -> refine is a no-op, winning candidate untouched."""

    async def proposer(_e: Dict[str, Any]) -> List[Dict[str, Any]]:
        return json.loads(json.dumps(two_candidates))

    critic_factory = lambda _mid: AsyncMock(  # noqa: E731
        return_value=_critic_json([], ["good"], "accept_as_is", 0.8)
    )

    moderator_mock = AsyncMock(
        return_value=_moderator_json(
            winning_index=0,
            reasoning=["x", "y", "z"],
            confidence=0.9,
            critique_signal="add detail",
        )
    )

    # Refine LLM returns garbage prose -> refine module produces no-op result.
    refine_garbage = AsyncMock(return_value="sorry I can't help with that")

    result = await run_internal_debate(
        event,
        propose_candidates_fn=proposer,
        critic_llm_factory=critic_factory,
        moderator_llm_factory=lambda _mid: moderator_mock,
        refine_llm=refine_garbage,
    )

    # final_candidate equals candidate 0 (winning candidate, no edits).
    winning = two_candidates[0]
    assert result.final_candidate == winning
    assert refine_garbage.await_count == 1
    # diff_summary explains the no-op.
    diff_text = " ".join(result.refine_result.diff_summary).lower()
    assert "malformed json" in diff_text or "original candidate kept" in diff_text


@pytest.mark.asyncio
async def test_candidate_hash_matches_final_candidate(
    event: Dict[str, Any], two_candidates: List[Dict[str, Any]]
) -> None:
    """Critical provenance property: hash(final_candidate) is reproducible
    and matches what BaseTranslatorAgent commits on-chain."""

    async def proposer(_e: Dict[str, Any]) -> List[Dict[str, Any]]:
        return json.loads(json.dumps(two_candidates))

    critic_factory = lambda _mid: AsyncMock(  # noqa: E731
        return_value=_critic_json([], ["good"], "accept_as_is", 0.8)
    )

    moderator_mock = AsyncMock(
        return_value=_moderator_json(
            winning_index=0,
            reasoning=["a", "b", "c"],
            confidence=0.9,
            critique_signal="be precise",
        )
    )

    # Refine emits a small, deterministic edit.
    refined = dict(two_candidates[0])
    refined["resolution_criteria"] = (
        "Resolves YES if the FOMC June 2026 statement on federalreserve.gov "
        "raises the federal funds upper bound by 25 bps before 2026-06-30T23:59:59Z."
    )
    # title / category / end_date_iso are preserved by refine.py contract.
    refine_mock = AsyncMock(return_value=json.dumps(refined))

    result = await run_internal_debate(
        event,
        propose_candidates_fn=proposer,
        critic_llm_factory=critic_factory,
        moderator_llm_factory=lambda _mid: moderator_mock,
        refine_llm=refine_mock,
    )

    # Compute the hash externally — exactly what an external operator would.
    expected_hash = _hash_candidate(result.final_candidate)

    # And compute it the way BaseTranslatorAgent does — must match.
    from polyglot_alpha.agents.base import BaseTranslatorAgent

    on_chain_hash = BaseTranslatorAgent.hash_candidate_dict(
        result.final_candidate
    )
    assert on_chain_hash.hex() == expected_hash

    # The hash MUST differ from a hash of the pre-refine winning candidate
    # whenever refine actually changed something — otherwise the
    # post-refine provenance is meaningless.
    pre_refine_hash = BaseTranslatorAgent.hash_candidate_dict(two_candidates[0])
    assert pre_refine_hash != on_chain_hash, (
        "candidate_hash should reflect the post-refine candidate, "
        "not the pre-debate translator output"
    )


@pytest.mark.asyncio
async def test_internal_debate_requires_two_candidates(
    event: Dict[str, Any], two_candidates: List[Dict[str, Any]]
) -> None:
    """Proposer returning <2 candidates raises ValueError."""

    async def lonely(_e: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [two_candidates[0]]

    with pytest.raises(ValueError, match="at least 2 candidates"):
        await run_internal_debate(event, propose_candidates_fn=lonely)
