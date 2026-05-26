"""Tests for the LLM-backed synthesizer + heuristic fallback path.

The synthesizer is invoked synchronously from inside the async pipeline, so
we test it the same way: plain ``def`` test functions with stubbed
``httpx.Client`` to avoid real network calls.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polyglot_alpha import synthesizer
from polyglot_alpha.schemas import NewsEvent, Question, TranslationCandidate


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


def _event() -> NewsEvent:
    return NewsEvent(
        event_id="evt-001",
        url="https://example.com/news/1",
        title_zh="人民银行宣布新的货币政策",
        body_zh="人民银行今日宣布将下调存款准备金率0.5个百分点。",
        cutoff_ts=1_800_000_000,
    )


def _candidate_a() -> TranslationCandidate:
    return TranslationCandidate(
        translator_id="gemini-2.0-flash",
        question_en="Will the People's Bank of China cut RRR?",
        resolution_criteria="Resolves YES if PBoC announces an RRR cut.",
        end_date_iso="2026-12-31T23:59:59Z",
        tags=["china", "monetary-policy"],
    )


def _candidate_b() -> TranslationCandidate:
    return TranslationCandidate(
        translator_id="deepseek-chat",
        question_en="Will the PBoC reduce the reserve requirement ratio?",
        resolution_criteria=(
            "Resolves YES if the People's Bank of China formally announces a "
            "reserve requirement ratio cut of any magnitude before the "
            "end_date, as reported by Reuters or Xinhua. Otherwise NO."
        ),
        end_date_iso="2026-06-30T23:59:59Z",
        tags=["china", "rrr", "macro"],
    )


def _fake_openrouter_response(payload: dict[str, Any]) -> MagicMock:
    """Build a stub ``httpx.Response`` carrying ``payload`` as JSON content."""

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={
            "choices": [
                {"message": {"content": json.dumps(payload)}}
            ]
        }
    )
    return resp


# --------------------------------------------------------------------------- #
# 1. LLM merge happy path.                                                    #
# --------------------------------------------------------------------------- #


def test_synthesizer_llm_merge_returns_combined_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM merges: A's wording + B's stronger resolution_criteria."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    merged_payload = {
        "question_en": "Will the People's Bank of China cut RRR?",
        "resolution_criteria": (
            "Resolves YES if the PBoC formally announces an RRR cut of any "
            "magnitude before the end_date, as reported by Reuters or Xinhua."
        ),
        "end_date_iso": "2026-12-31T23:59:59Z",
    }
    fake_resp = _fake_openrouter_response(merged_payload)

    client_cm = MagicMock()
    client_cm.__enter__ = MagicMock(return_value=client_cm)
    client_cm.__exit__ = MagicMock(return_value=False)
    client_cm.post = MagicMock(return_value=fake_resp)

    with patch.object(synthesizer.httpx, "Client", return_value=client_cm):
        result = synthesizer.synthesize(
            _event(), [_candidate_a(), _candidate_b()]
        )

    assert isinstance(result, Question)
    # A's wording survived...
    assert result.question_en == "Will the People's Bank of China cut RRR?"
    # ... but B's stronger resolution_criteria was adopted.
    assert "Reuters or Xinhua" in result.resolution_criteria
    # event_id is wired from the event, not from the LLM payload.
    assert result.event_id == "evt-001"
    # The LLM was actually invoked (not the heuristic).
    client_cm.post.assert_called_once()


# --------------------------------------------------------------------------- #
# 2. Fallback path: LLM failure -> heuristic + WARNING log.                   #
# --------------------------------------------------------------------------- #


def test_synthesizer_falls_back_to_heuristic_on_llm_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HTTP error -> heuristic (longest resolution_criteria) + WARNING."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    import httpx

    client_cm = MagicMock()
    client_cm.__enter__ = MagicMock(return_value=client_cm)
    client_cm.__exit__ = MagicMock(return_value=False)
    client_cm.post = MagicMock(
        side_effect=httpx.ConnectError("synthetic network failure")
    )

    cand_a = _candidate_a()
    cand_b = _candidate_b()  # B has the longer resolution_criteria.

    with caplog.at_level(logging.WARNING, logger="polyglot_alpha.synthesizer"):
        with patch.object(synthesizer.httpx, "Client", return_value=client_cm):
            result = synthesizer.synthesize(_event(), [cand_a, cand_b])

    # Heuristic picks B (its resolution_criteria is longer than A's).
    assert result.resolution_criteria == cand_b.resolution_criteria
    assert result.question_en == cand_b.question_en
    assert result.end_date_iso == cand_b.end_date_iso

    # Fallback is loudly announced — NOT silently swallowed.
    warning_messages = [
        rec.message for rec in caplog.records if rec.levelno == logging.WARNING
    ]
    assert any("LLM HTTP call failed" in m for m in warning_messages), (
        warning_messages
    )
    assert any(
        "falling back to heuristic" in m for m in warning_messages
    ), warning_messages


def test_synthesizer_falls_back_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No backend keys -> heuristic + WARNING (no HTTP call made)."""

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with caplog.at_level(logging.WARNING, logger="polyglot_alpha.synthesizer"):
        # Patch Client so a runaway real HTTP call would explode the test.
        with patch.object(synthesizer.httpx, "Client") as client_factory:
            result = synthesizer.synthesize(
                _event(), [_candidate_a(), _candidate_b()]
            )
            client_factory.assert_not_called()

    warning_messages = [
        rec.message for rec in caplog.records if rec.levelno == logging.WARNING
    ]
    assert any(
        "ANTHROPIC_API_KEY" in m or "OPENROUTER_API_KEY" in m
        for m in warning_messages
    ), warning_messages
    assert any(
        "falling back to heuristic" in m for m in warning_messages
    ), warning_messages
    # Result still well-formed.
    assert isinstance(result, Question)
    assert result.resolution_criteria  # populated from a candidate


# --------------------------------------------------------------------------- #
# 3. Output shape preservation.                                               #
# --------------------------------------------------------------------------- #


def test_synthesizer_preserves_required_fields_in_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The returned Question always carries event_id + 3 merge fields."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    merged_payload = {
        "question_en": "Will the PBoC cut RRR by Q4 2026?",
        "resolution_criteria": (
            "Resolves YES if the PBoC announces an RRR cut before "
            "2026-12-31."
        ),
        "end_date_iso": "2026-12-31T23:59:59Z",
    }
    fake_resp = _fake_openrouter_response(merged_payload)

    client_cm = MagicMock()
    client_cm.__enter__ = MagicMock(return_value=client_cm)
    client_cm.__exit__ = MagicMock(return_value=False)
    client_cm.post = MagicMock(return_value=fake_resp)

    with patch.object(synthesizer.httpx, "Client", return_value=client_cm):
        result = synthesizer.synthesize(
            _event(), [_candidate_a(), _candidate_b()]
        )

    # Required fields from the Question schema are all populated.
    assert result.event_id == "evt-001"
    assert result.question_en
    assert result.resolution_criteria
    assert result.end_date_iso
    # Defaults from the schema survive.
    assert result.yes_outcome == "YES"
    assert result.no_outcome == "NO"


def test_synthesizer_falls_back_when_llm_returns_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed JSON from the LLM -> heuristic + WARNING."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={
            "choices": [
                {"message": {"content": "not actually json {{{"}}
            ]
        }
    )

    client_cm = MagicMock()
    client_cm.__enter__ = MagicMock(return_value=client_cm)
    client_cm.__exit__ = MagicMock(return_value=False)
    client_cm.post = MagicMock(return_value=resp)

    with caplog.at_level(logging.WARNING, logger="polyglot_alpha.synthesizer"):
        with patch.object(synthesizer.httpx, "Client", return_value=client_cm):
            result = synthesizer.synthesize(
                _event(), [_candidate_a(), _candidate_b()]
            )

    assert isinstance(result, Question)
    warning_messages = [
        rec.message for rec in caplog.records if rec.levelno == logging.WARNING
    ]
    assert any(
        "unparseable JSON" in m for m in warning_messages
    ), warning_messages


def test_synthesizer_raises_on_empty_candidates() -> None:
    """Empty candidate list is a programming error -> ValueError."""

    with pytest.raises(ValueError, match="at least one candidate"):
        synthesizer.synthesize(_event(), [])


def test_synthesizer_single_candidate_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One candidate -> no LLM call (nothing to merge)."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    with patch.object(synthesizer.httpx, "Client") as client_factory:
        result = synthesizer.synthesize(_event(), [_candidate_a()])
        client_factory.assert_not_called()

    assert result.question_en == _candidate_a().question_en
