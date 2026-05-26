"""Unit tests for the L3 (critic) and L4 (moderator) debate stages.

All tests stay offline: the LLM callables are replaced with
``unittest.mock.AsyncMock`` returning canned JSON strings. No network /
``OPENROUTER_API_KEY`` is required.

Run with: ``.venv/bin/pytest -xvs tests/test_critics_moderator.py``
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from polyglot_alpha.agents.critics import (
    CRITIC_MODEL_A,
    CRITIC_MODEL_B,
    CritiqueResult,
    run_critic_round,
)
from polyglot_alpha.agents.moderator import (
    MODERATOR_MODEL,
    ModeratorVerdict,
    run_moderator,
)


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def event() -> Dict[str, Any]:
    return {
        "event_id": "0xabc",
        "title_zh": "美联储宣布加息25个基点",
        "body_zh": "美联储于2026年5月议息会议宣布加息25个基点，市场预期下次议息会议……",
        "url": "https://example.com/fed",
        "cutoff_ts": 1735689600,
    }


@pytest.fixture()
def candidates() -> List[Dict[str, Any]]:
    """Two translator candidates. Candidate 1 is intentionally weaker
    (vaguer resolution criteria) so we can verify the moderator picks 0."""

    return [
        {
            "translator_id": "t0",
            "question_en": "Will the Fed raise rates by 25bps at the June 2026 FOMC meeting?",
            "resolution_criteria": (
                "Resolves YES if the Federal Reserve's June 2026 FOMC statement "
                "raises the federal funds target range by exactly 25 basis points, "
                "as published on federalreserve.gov by 2026-06-30T23:59:59Z. "
                "Otherwise NO."
            ),
            "end_date_iso": "2026-06-30T23:59:59Z",
            "tags": ["macro", "fed", "rates"],
        },
        {
            "translator_id": "t1",
            "question_en": "Will the Fed hike soon?",
            "resolution_criteria": "Resolves YES if the Fed hikes.",
            "end_date_iso": "2026-12-31T23:59:59Z",
            "tags": ["fed"],
        },
    ]


def _critic_json(
    issues: List[str],
    strengths: List[str],
    verdict: str,
    confidence: float,
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


# --------------------------------------------------------------------------- #
# Critic-round tests                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_critic_round_parallel_two_candidates(
    event: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> None:
    """Two critics return DIFFERENT verdicts; results are ordered by target_index."""

    # Critic A (cheap model A) reviews candidate B (index 1) — finds it weak.
    critic_a_response = _critic_json(
        issues=[
            "vague 'soon' is ambiguous and unmeasurable",
            "resolution_criteria omits source and date",
            "end_date is 6 months past the relevant FOMC",
        ],
        strengths=["concise wording"],
        verdict="needs_refinement",
        confidence=0.85,
    )
    # Critic B (cheap model B) reviews candidate A (index 0) — finds it solid.
    critic_b_response = _critic_json(
        issues=["tags could include 'monetary-policy'"],
        strengths=[
            "explicit resolution source",
            "precise 25bps threshold",
            "clear cutoff",
        ],
        verdict="accept_as_is",
        confidence=0.92,
    )

    critic_a_mock = AsyncMock(return_value=critic_a_response)
    critic_b_mock = AsyncMock(return_value=critic_b_response)

    def factory(model_id: str):
        if model_id == CRITIC_MODEL_A:
            return critic_a_mock
        if model_id == CRITIC_MODEL_B:
            return critic_b_mock
        raise AssertionError(f"unexpected model_id: {model_id}")

    results = await run_critic_round(candidates, event, llm_factory=factory)

    # Two critic calls — confirms parallelism worked (gather completed both).
    assert critic_a_mock.await_count == 1
    assert critic_b_mock.await_count == 1

    # Results ordered by target_index.
    assert [r.target_index for r in results] == [0, 1]

    # Review of candidate 0 came from CRITIC_MODEL_B.
    review_of_0 = results[0]
    assert review_of_0.critic_model == CRITIC_MODEL_B
    assert review_of_0.verdict == "accept_as_is"
    assert review_of_0.confidence == pytest.approx(0.92)
    assert "precise 25bps threshold" in review_of_0.strengths

    # Review of candidate 1 came from CRITIC_MODEL_A.
    review_of_1 = results[1]
    assert review_of_1.critic_model == CRITIC_MODEL_A
    assert review_of_1.verdict == "needs_refinement"
    assert review_of_1.confidence == pytest.approx(0.85)
    assert any("vague" in i for i in review_of_1.issues)

    # Verdicts differ — critics did NOT agree, which is the diversity we want.
    assert review_of_0.verdict != review_of_1.verdict


@pytest.mark.asyncio
async def test_critic_timeout_soft_skip(
    event: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> None:
    """Critic that hangs past the timeout should soft-fail to accept_as_is."""

    async def slow_llm(prompt: str) -> str:
        await asyncio.sleep(5.0)
        return _critic_json([], [], "reject", 1.0)

    fast_response = _critic_json(
        issues=["minor: missing source"],
        strengths=["clear"],
        verdict="accept_as_is",
        confidence=0.7,
    )

    fast_mock = AsyncMock(return_value=fast_response)

    def factory(model_id: str):
        if model_id == CRITIC_MODEL_A:
            return slow_llm  # this critic will time out
        return fast_mock

    # Use a tight timeout so the test runs in well under a second.
    results = await run_critic_round(
        candidates, event, llm_factory=factory, timeout=0.05
    )

    # Result for candidate 1 (reviewed by CRITIC_MODEL_A, which hung) soft-failed.
    review_of_1 = results[1]
    assert review_of_1.target_index == 1
    assert review_of_1.critic_model == CRITIC_MODEL_A
    assert review_of_1.verdict == "accept_as_is"
    assert review_of_1.issues == []
    assert review_of_1.confidence == 0.0
    assert "soft-fail" in review_of_1.raw_response

    # Other critic completed normally.
    review_of_0 = results[0]
    assert review_of_0.target_index == 0
    assert review_of_0.critic_model == CRITIC_MODEL_B
    assert review_of_0.verdict == "accept_as_is"
    assert review_of_0.confidence == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_critic_handles_unparseable_response(
    event: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> None:
    """Critic that returns garbage prose still produces a CritiqueResult with
    safe defaults rather than crashing."""

    garbage_mock = AsyncMock(return_value="sorry I can't help with that")
    good_mock = AsyncMock(
        return_value=_critic_json(["minor"], ["clear"], "accept_as_is", 0.6)
    )

    def factory(model_id: str):
        if model_id == CRITIC_MODEL_A:
            return garbage_mock
        return good_mock

    results = await run_critic_round(candidates, event, llm_factory=factory)

    assert len(results) == 2
    # Garbage critique falls back to neutral defaults but DOES NOT raise.
    review_of_1 = results[1]
    assert review_of_1.verdict == "accept_as_is"
    assert review_of_1.issues == []
    assert review_of_1.strengths == []
    assert review_of_1.confidence == 0.0
    # Raw response is preserved for debugging.
    assert "sorry" in review_of_1.raw_response


# --------------------------------------------------------------------------- #
# Moderator tests                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_moderator_picks_higher_quality_candidate(
    event: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> None:
    """Moderator should pick candidate 0 (the precise one) given the critic signals."""

    critiques = [
        CritiqueResult(
            target_index=0,
            critic_model=CRITIC_MODEL_B,
            issues=["tags could include 'monetary-policy'"],
            strengths=["precise 25bps threshold", "explicit source"],
            verdict="accept_as_is",
            confidence=0.92,
            raw_response="(stub)",
        ),
        CritiqueResult(
            target_index=1,
            critic_model=CRITIC_MODEL_A,
            issues=[
                "vague 'soon' is ambiguous",
                "resolution_criteria omits source",
                "end_date is too far out",
            ],
            strengths=["concise"],
            verdict="needs_refinement",
            confidence=0.85,
            raw_response="(stub)",
        ),
    ]

    moderator_response = _moderator_json(
        winning_index=0,
        reasoning=[
            "candidate 0 has explicit federalreserve.gov source",
            "candidate 0 specifies exact 25bps threshold",
            "candidate 1's 'soon' is unmeasurable",
            "candidate 0's cutoff matches the relevant FOMC meeting",
        ],
        confidence=0.88,
        critique_signal=(
            "Add 'monetary-policy' tag and clarify that the 25bps target range "
            "applies to the upper bound of the federal funds rate."
        ),
    )

    moderator_mock = AsyncMock(return_value=moderator_response)

    def factory(model_id: str):
        assert model_id == MODERATOR_MODEL
        return moderator_mock

    verdict = await run_moderator(
        candidates, critiques, event, llm_factory=factory
    )

    assert isinstance(verdict, ModeratorVerdict)
    assert verdict.winning_index == 0
    assert verdict.moderator_model == MODERATOR_MODEL
    assert len(verdict.reasoning) >= 3
    assert verdict.confidence == pytest.approx(0.88)
    assert "monetary-policy" in verdict.critique_signal
    assert moderator_mock.await_count == 1


@pytest.mark.asyncio
async def test_moderator_fallback_when_sonnet_fails(
    event: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> None:
    """Moderator LLM raises — fallback picks higher-confidence critic's target."""

    critiques = [
        CritiqueResult(
            target_index=0,
            critic_model=CRITIC_MODEL_B,
            issues=["could be more concise"],
            strengths=["explicit source"],
            verdict="accept_as_is",
            confidence=0.95,  # higher confidence
            raw_response="(stub)",
        ),
        CritiqueResult(
            target_index=1,
            critic_model=CRITIC_MODEL_A,
            issues=["vague", "no source"],
            strengths=[],
            verdict="needs_refinement",
            confidence=0.40,
            raw_response="(stub)",
        ),
    ]

    async def boom(prompt: str) -> str:
        raise RuntimeError("openrouter 500: upstream model unavailable")

    def factory(model_id: str):
        return boom

    verdict = await run_moderator(
        candidates, critiques, event, llm_factory=factory
    )

    # Higher-confidence non-rejecting critic targeted candidate 0 → wins.
    assert verdict.winning_index == 0
    assert verdict.moderator_model == "(fallback)"
    assert verdict.confidence == pytest.approx(0.95)
    assert any("fallback" in r for r in verdict.reasoning)
    # Signal should reflect the winning critic's issues.
    assert "could be more concise" in verdict.critique_signal
    assert "[fallback:" in verdict.raw_response


@pytest.mark.asyncio
async def test_moderator_fallback_both_need_refinement(
    event: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> None:
    """When LLM dies AND both critics said needs_refinement, default to candidate 0
    with the spec-defined signal string."""

    critiques = [
        CritiqueResult(
            target_index=0,
            critic_model=CRITIC_MODEL_B,
            issues=["x"],
            strengths=[],
            verdict="needs_refinement",
            confidence=0.5,
            raw_response="(stub)",
        ),
        CritiqueResult(
            target_index=1,
            critic_model=CRITIC_MODEL_A,
            issues=["y"],
            strengths=[],
            verdict="needs_refinement",
            confidence=0.5,
            raw_response="(stub)",
        ),
    ]

    async def boom(prompt: str) -> str:
        raise RuntimeError("nope")

    verdict = await run_moderator(
        candidates, critiques, event, llm_factory=lambda mid: boom
    )

    assert verdict.winning_index == 0
    assert verdict.moderator_model == "(fallback)"
    assert verdict.critique_signal == (
        "moderator unavailable; using critic A's feedback"
    )


@pytest.mark.asyncio
async def test_moderator_timeout_triggers_fallback(
    event: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> None:
    """Moderator hanging past timeout should produce a fallback verdict."""

    critiques = [
        CritiqueResult(
            target_index=0,
            critic_model=CRITIC_MODEL_B,
            issues=[],
            strengths=["good"],
            verdict="accept_as_is",
            confidence=0.8,
            raw_response="(stub)",
        ),
        CritiqueResult(
            target_index=1,
            critic_model=CRITIC_MODEL_A,
            issues=["bad"],
            strengths=[],
            verdict="reject",
            confidence=0.9,
            raw_response="(stub)",
        ),
    ]

    async def slow(prompt: str) -> str:
        await asyncio.sleep(5.0)
        return _moderator_json(1, ["x", "y", "z"], 1.0, "signal")

    verdict = await run_moderator(
        candidates,
        critiques,
        event,
        llm_factory=lambda mid: slow,
        timeout=0.05,
    )

    # Critic 1 verdict was 'reject', so fallback should NOT pick candidate 1.
    assert verdict.winning_index == 0
    assert verdict.moderator_model == "(fallback)"
    assert "timeout" in verdict.raw_response


@pytest.mark.asyncio
async def test_moderator_unparseable_response_triggers_fallback(
    event: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> None:
    """Moderator returning non-JSON falls back rather than crashing."""

    critiques = [
        CritiqueResult(
            target_index=0,
            critic_model=CRITIC_MODEL_B,
            issues=[],
            strengths=["good"],
            verdict="accept_as_is",
            confidence=0.7,
            raw_response="(stub)",
        ),
        CritiqueResult(
            target_index=1,
            critic_model=CRITIC_MODEL_A,
            issues=["bad"],
            strengths=[],
            verdict="accept_as_is",
            confidence=0.5,
            raw_response="(stub)",
        ),
    ]

    garbage_mock = AsyncMock(return_value="I refuse to answer.")

    verdict = await run_moderator(
        candidates, critiques, event, llm_factory=lambda mid: garbage_mock
    )

    # Higher-confidence non-rejecting critic targeted candidate 0 → wins.
    assert verdict.winning_index == 0
    assert verdict.moderator_model == "(fallback)"
    assert "unparseable_response" in verdict.raw_response


# --------------------------------------------------------------------------- #
# Integration-ish smoke test                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_critic_then_moderator_end_to_end(
    event: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> None:
    """Chained call: critics produce different verdicts, moderator picks winner.

    This is the trace the parent agent's report references.
    """

    # Critic A reviews candidate B (index 1) — verdict: needs_refinement
    critic_a_response = _critic_json(
        issues=["'soon' is ambiguous", "no source", "end_date drift"],
        strengths=["concise"],
        verdict="needs_refinement",
        confidence=0.84,
    )
    # Critic B reviews candidate A (index 0) — verdict: accept_as_is
    critic_b_response = _critic_json(
        issues=["minor: add monetary-policy tag"],
        strengths=["precise threshold", "explicit source"],
        verdict="accept_as_is",
        confidence=0.91,
    )

    def critic_factory(model_id: str):
        if model_id == CRITIC_MODEL_A:
            return AsyncMock(return_value=critic_a_response)
        if model_id == CRITIC_MODEL_B:
            return AsyncMock(return_value=critic_b_response)
        raise AssertionError(model_id)

    critiques = await run_critic_round(
        candidates, event, llm_factory=critic_factory
    )

    # Sanity: critics disagreed.
    verdicts = sorted(c.verdict for c in critiques)
    assert verdicts == ["accept_as_is", "needs_refinement"]

    moderator_response = _moderator_json(
        winning_index=0,
        reasoning=[
            "candidate 0 satisfies all critic-flagged dimensions",
            "candidate 1 has unmeasurable 'soon'",
            "critic confidence favors candidate 0 (0.91 vs 0.84)",
        ],
        confidence=0.9,
        critique_signal="Add monetary-policy tag and explicitly note the upper-bound interpretation.",
    )

    def mod_factory(model_id: str):
        return AsyncMock(return_value=moderator_response)

    verdict = await run_moderator(
        candidates, critiques, event, llm_factory=mod_factory
    )

    assert verdict.winning_index == 0
    assert len(verdict.reasoning) >= 3
    assert "monetary-policy" in verdict.critique_signal
