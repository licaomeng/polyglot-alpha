"""Tests for the marketplace-side event-scoring layer.

The marketplace MUST NOT write Polymarket question text. These tests
pin down the contract:
  * :func:`score_event_for_auction` never raises and always returns an
    :class:`EventScoring` dataclass.
  * The output has no ``polymarket_question`` / ``resolution_criteria``
    / ``selected_index`` field.
  * Sub-threshold scores produce a non-empty ``rejection_reason``.
  * Heuristic fallback fires when the SDK / API key / network is absent.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any
from unittest.mock import MagicMock

import pytest

from polyglot_alpha.ingestion import news_summarizer
from polyglot_alpha.ingestion.news_summarizer import (
    EventScoring,
    MIN_AUCTION_QUALITY,
    score_event_for_auction,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_ARTICLES: list[dict[str, Any]] = [
    {
        "title": "PBOC signals possible RRR cut in coming weeks",
        "summary": (
            "People's Bank of China governor hinted at a 25bp reserve "
            "requirement ratio cut to support liquidity."
        ),
        "source": "reuters",
        "published": "2026-05-26T10:00:00Z",
        "url": "https://example.com/pboc-rrr",
        "language": "en",
    },
    {
        "title": "央行暗示近期可能降准",
        "summary": "中国人民银行行长表示可能下调存款准备金率以支持流动性。",
        "source": "xinhua",
        "published": "2026-05-26T09:30:00Z",
        "url": "https://example.com/xinhua-rrr",
        "language": "zh",
    },
]


def _haiku_text_response(payload: dict[str, Any]) -> MagicMock:
    """Build a fake anthropic SDK response with ``content[0].text=JSON``."""

    import json as _json

    block = MagicMock()
    block.text = _json.dumps(payload)
    resp = MagicMock()
    resp.content = [block]
    return resp


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---------------------------------------------------------------------------
# Contract: forbidden fields are gone.
# ---------------------------------------------------------------------------


def test_event_scoring_dataclass_has_no_question_fields() -> None:
    """EventScoring must not carry any question / resolution / cutoff text."""

    fields = set(EventScoring.__dataclass_fields__)
    forbidden = {
        "polymarket_question",
        "resolution_criteria",
        "resolution_source",
        "cutoff_iso",
        "selected_index",
        "candidates",
    }
    leaked = fields & forbidden
    assert not leaked, f"EventScoring leaked forbidden fields: {leaked}"


def test_module_does_not_export_legacy_summarizer() -> None:
    """The old ``summarize_news_for_polymarket`` symbol must be gone."""

    assert not hasattr(news_summarizer, "summarize_news_for_polymarket")


# ---------------------------------------------------------------------------
# Heuristic fallback path (no SDK / no API key).
# ---------------------------------------------------------------------------


def test_empty_articles_returns_zero_score_rejection() -> None:
    result = asyncio.run(score_event_for_auction([]))
    assert isinstance(result, EventScoring)
    assert result.event_quality_score == 0.0
    assert result.rejection_reason


def test_no_api_key_returns_heuristic_with_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = asyncio.run(score_event_for_auction(SAMPLE_ARTICLES))
    assert isinstance(result, EventScoring)
    assert result.event_quality_score < MIN_AUCTION_QUALITY
    assert result.rejection_reason
    # raw_summary should be populated from the first article.
    assert SAMPLE_ARTICLES[0]["title"] in result.raw_summary
    assert result.model == "heuristic_fallback"


# ---------------------------------------------------------------------------
# Happy-path: mocked Haiku client.
# ---------------------------------------------------------------------------


def test_valid_haiku_response_produces_scoring() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _haiku_text_response(
        {
            "event_quality_score": 0.85,
            "primary_category": "macro/china_monetary",
            "sub_categories": ["rates", "liquidity"],
            "key_entities": ["PBOC", "RRR"],
            "source_credibility": 0.9,
            "timeliness_score": 0.95,
            "raw_summary": (
                "The People's Bank of China signaled a possible 25bp "
                "reserve requirement ratio cut."
            ),
            "rejection_reason": None,
        }
    )

    result = asyncio.run(
        score_event_for_auction(SAMPLE_ARTICLES, anthropic_client=fake_client)
    )

    assert isinstance(result, EventScoring)
    assert result.event_quality_score == 0.85
    assert result.primary_category == "macro/china_monetary"
    assert "PBOC" in result.key_entities
    assert result.rejection_reason is None
    assert result.source_credibility == 0.9

    # Sanity-check the prompt: it must NOT ask for question text.
    call_kwargs = fake_client.messages.create.call_args.kwargs
    prompt_text = call_kwargs["messages"][0]["content"]
    assert "polymarket_question" not in prompt_text
    assert "resolution_criteria" not in prompt_text
    assert "selected_index" not in prompt_text


def test_sub_threshold_score_gets_synthetic_rejection_reason() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _haiku_text_response(
        {
            "event_quality_score": 0.2,
            "primary_category": "other",
            "sub_categories": [],
            "key_entities": [],
            "source_credibility": 0.4,
            "timeliness_score": 0.5,
            "raw_summary": "Opinion column on cultural policy.",
            # Model forgot to set rejection_reason — we must synthesize one.
            "rejection_reason": None,
        }
    )

    result = asyncio.run(
        score_event_for_auction(SAMPLE_ARTICLES, anthropic_client=fake_client)
    )
    assert result.event_quality_score == 0.2
    assert result.rejection_reason is not None
    assert "below auction threshold" in result.rejection_reason


def test_score_clamping_handles_out_of_range_values() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _haiku_text_response(
        {
            "event_quality_score": 1.5,  # >1.0
            "primary_category": "geopolitics",
            "sub_categories": [],
            "key_entities": [],
            "source_credibility": -0.3,  # <0.0
            "timeliness_score": "not_a_number",  # bogus
            "raw_summary": "x",
            "rejection_reason": None,
        }
    )
    result = asyncio.run(
        score_event_for_auction(SAMPLE_ARTICLES, anthropic_client=fake_client)
    )
    assert result.event_quality_score == 1.0
    assert result.source_credibility == 0.0
    # bogus -> default 0.5
    assert result.timeliness_score == 0.5


def test_malformed_json_falls_back_to_heuristic() -> None:
    fake_client = MagicMock()
    block = MagicMock()
    block.text = "this is not JSON at all"
    resp = MagicMock()
    resp.content = [block]
    fake_client.messages.create.return_value = resp

    result = asyncio.run(
        score_event_for_auction(SAMPLE_ARTICLES, anthropic_client=fake_client)
    )
    assert result.model == "heuristic_fallback"
    assert result.rejection_reason


def test_sdk_exception_falls_back_to_heuristic() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("network down")

    result = asyncio.run(
        score_event_for_auction(SAMPLE_ARTICLES, anthropic_client=fake_client)
    )
    assert result.model == "heuristic_fallback"
    assert result.rejection_reason


# ---------------------------------------------------------------------------
# Serialization: the ``scoring`` dict that lands in event_dict.
# ---------------------------------------------------------------------------


def test_as_dict_round_trips_without_forbidden_keys() -> None:
    scoring = EventScoring(
        event_quality_score=0.7,
        primary_category="macro/china",
        sub_categories=["rates"],
        key_entities=["PBOC"],
        source_credibility=0.8,
        timeliness_score=0.9,
        raw_summary="neutral summary",
        rejection_reason=None,
    )
    d = scoring.as_dict()
    assert d["event_quality_score"] == 0.7
    for forbidden in (
        "polymarket_question",
        "resolution_criteria",
        "cutoff_iso",
        "selected_index",
        "candidates",
    ):
        assert forbidden not in d
