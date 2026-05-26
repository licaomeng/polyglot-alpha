"""Unit tests for the Layer 5 refine module.

All tests stay fully offline: the LLM is injected as an in-process async
callable so no network calls fire.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Dict

import pytest

from polyglot_alpha.agents.refine import (
    DEFAULT_REFINE_TIMEOUT_S,
    PRESERVED_FIELDS,
    RefineResult,
    refine_with_critique,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def event() -> Dict[str, Any]:
    return {
        "event_id": "evt_refine_001",
        "title_zh": "中国宣布新关税政策",
        "body_zh": "中国财政部宣布将就关税政策做出回应。",
    }


@pytest.fixture()
def winning_candidate() -> Dict[str, Any]:
    """A representative moderator-picked candidate dict."""

    return {
        "translator_id": "t0",
        "title": "Will China announce new tariffs by 2026-06-30?",
        "question_en": "Will China announce new tariffs by 2026-06-30?",
        "category": "geopolitics",
        "end_date_iso": "2026-06-30T23:59:59Z",
        "resolution_criteria": "Resolves YES if tariffs are announced.",
        "resolution_source": "",
        "tags": ["china", "tariffs"],
        "meta": {"model": "deepseek/deepseek-chat"},
    }


def _llm_returning(payload: Any) -> Callable[[str], Awaitable[str]]:
    """Build an LLM stub that returns a fixed string."""

    text = payload if isinstance(payload, str) else json.dumps(payload)

    async def _call(_prompt: str) -> str:
        return text

    return _call


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_refine_returns_revised_question(
    winning_candidate: Dict[str, Any], event: Dict[str, Any]
) -> None:
    revised = {
        # The LLM tries to mutate the title — refine must restore it.
        "title": "DIFFERENT TITLE (should be reverted)",
        "question_en": "Will China's Ministry of Finance announce new tariffs by 2026-06-30?",
        "category": "geopolitics",
        "end_date_iso": "2026-06-30T23:59:59Z",
        "resolution_criteria": (
            "Resolves YES if an official report by China's Ministry of "
            "Finance announces new tariffs before 2026-06-30T23:59:59Z."
        ),
        "resolution_source": "https://www.mof.gov.cn/",
        "tags": ["china", "tariffs", "trade-policy"],
    }

    result = asyncio.run(
        refine_with_critique(
            winning_candidate,
            critique_signal=(
                "Resolution criteria are too vague; name the official "
                "source and the exact cutoff."
            ),
            event=event,
            llm=_llm_returning(revised),
        )
    )

    assert isinstance(result, RefineResult)
    assert result.refine_model == "deepseek/deepseek-chat"
    # Edited fields propagate.
    assert (
        result.refined_question["resolution_criteria"]
        == revised["resolution_criteria"]
    )
    assert result.refined_question["resolution_source"] == "https://www.mof.gov.cn/"
    # Identity fields are forcibly preserved.
    assert (
        result.refined_question["title"]
        == winning_candidate["title"]
    )
    assert result.duration_ms >= 0
    assert result.raw_response  # raw LLM text retained


def test_refine_preserves_required_fields(
    winning_candidate: Dict[str, Any], event: Dict[str, Any]
) -> None:
    """title / category / end_date_iso must survive any LLM mutation."""

    revised = {
        "title": "MALICIOUS REWRITE",
        "category": "MALICIOUS REWRITE",
        "end_date_iso": "2099-01-01T00:00:00Z",
        "question_en": "Refined wording.",
        "resolution_criteria": "Refined criteria.",
        "resolution_source": "https://example.com/",
        "tags": [],
    }

    result = asyncio.run(
        refine_with_critique(
            winning_candidate,
            critique_signal="Add an official source.",
            event=event,
            llm=_llm_returning(revised),
        )
    )

    for field_name in PRESERVED_FIELDS:
        assert (
            result.refined_question[field_name]
            == winning_candidate[field_name]
        ), f"refine must not mutate preserved field {field_name!r}"


def test_refine_no_op_on_malformed_json(
    winning_candidate: Dict[str, Any], event: Dict[str, Any]
) -> None:
    result = asyncio.run(
        refine_with_critique(
            winning_candidate,
            critique_signal="Tighten the criteria.",
            event=event,
            llm=_llm_returning("this is not JSON at all, just prose"),
        )
    )

    assert result.refined_question == winning_candidate
    assert any(
        "malformed JSON" in bullet for bullet in result.diff_summary
    ), result.diff_summary
    assert result.refine_model == "deepseek/deepseek-chat"


def test_refine_no_op_on_timeout(
    winning_candidate: Dict[str, Any], event: Dict[str, Any]
) -> None:
    async def _hang(_prompt: str) -> str:
        await asyncio.sleep(10)
        return "{}"

    result = asyncio.run(
        refine_with_critique(
            winning_candidate,
            critique_signal="Improve precision.",
            event=event,
            llm=_hang,
            timeout_s=0.05,
        )
    )

    assert result.refined_question == winning_candidate
    assert any("timed out" in bullet for bullet in result.diff_summary), (
        result.diff_summary
    )


def test_diff_summary_detects_resolution_criteria_changes(
    winning_candidate: Dict[str, Any], event: Dict[str, Any]
) -> None:
    """diff_summary must surface ``official report by`` and similar precision
    markers added to the resolution_criteria."""

    revised = dict(winning_candidate)
    revised["resolution_criteria"] = (
        "Resolves YES if an official report by China's Ministry of Finance "
        "announces tariffs before 2026-06-30T23:59:59Z."
    )

    result = asyncio.run(
        refine_with_critique(
            winning_candidate,
            critique_signal="Name the official source.",
            event=event,
            llm=_llm_returning(revised),
        )
    )

    joined = " | ".join(result.diff_summary)
    assert "official report by" in joined, result.diff_summary
    assert "precision markers" in joined, result.diff_summary


def test_model_id_resolution_uses_explicit_override(
    winning_candidate: Dict[str, Any], event: Dict[str, Any]
) -> None:
    """When ``model_id`` is passed, it overrides ``meta.model``."""

    result = asyncio.run(
        refine_with_critique(
            winning_candidate,
            critique_signal="Improve precision.",
            event=event,
            model_id="qwen/qwen-2.5-72b-instruct",
            llm=_llm_returning(dict(winning_candidate)),
        )
    )
    assert result.refine_model == "qwen/qwen-2.5-72b-instruct"


def test_model_id_resolution_falls_back_when_meta_missing(
    event: Dict[str, Any],
) -> None:
    """No meta.model + no explicit override falls back to the default."""

    bare_candidate = {
        "title": "Will it rain by 2026-06-30?",
        "category": "weather",
        "end_date_iso": "2026-06-30T23:59:59Z",
        "question_en": "Will it rain by 2026-06-30?",
        "resolution_criteria": "Resolves YES if it rains.",
    }
    result = asyncio.run(
        refine_with_critique(
            bare_candidate,
            critique_signal="add a source",
            event=event,
            llm=_llm_returning(dict(bare_candidate)),
        )
    )
    # Default fallback documented in refine.py — Anthropic Haiku 4.5
    # after the OpenRouter swap.
    from polyglot_alpha.llm import CLAUDE_HAIKU

    assert result.refine_model == CLAUDE_HAIKU


def test_default_timeout_constant() -> None:
    assert DEFAULT_REFINE_TIMEOUT_S == 45.0
