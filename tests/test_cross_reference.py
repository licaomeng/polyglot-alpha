"""Cross-reference clustering tests (mocked LLM)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from polyglot_alpha.ingestion.cross_reference import (
    content_hash,
    cluster_with_llm,
    cross_reference,
    filter_recent,
    heuristic_cluster,
)
from polyglot_alpha.ingestion.models import RawEvent


def _ev(source: str, title: str, summary: str = "", lang: str = "zh", url: str | None = None) -> RawEvent:
    return RawEvent(
        source=source,
        title=title,
        summary=summary,
        url=url or f"https://example.com/{source.lower()}/{abs(hash(title)) % 1000}",
        published_at=datetime.now(tz=timezone.utc),
        language=lang,
    )


def test_content_hash_is_stable_and_url_order_independent() -> None:
    h1 = content_hash("PBOC cuts RRR", ["https://a.com/1", "https://b.com/2"])
    h2 = content_hash("PBOC cuts RRR", ["https://b.com/2", "https://a.com/1"])
    assert h1 == h2
    assert len(h1) == 64


def test_filter_recent_drops_old_events() -> None:
    fresh = _ev("Caixin", "新鲜事件 - PBOC cuts RRR fresh")
    stale = RawEvent(
        source="Reuters",
        title="Old news",
        summary="",
        url="https://reuters.com/old",
        published_at=datetime.now(tz=timezone.utc) - timedelta(hours=5),
        language="en",
    )
    kept = filter_recent([fresh, stale], window=timedelta(hours=1))
    assert kept == [fresh]


def test_heuristic_cluster_requires_two_sources() -> None:
    same_source_a = _ev("Caixin", "PBOC announces RRR cut to support economy growth")
    same_source_b = _ev("Caixin", "PBOC announces RRR cut to support economy growth")
    different_source = _ev(
        "Xinhua", "PBOC announces RRR cut to support economy growth"
    )

    # Two events from same source -> no confirmation.
    assert heuristic_cluster([same_source_a, same_source_b]) == []

    # Two distinct sources sharing tokens -> one confirmed event.
    confirmed = heuristic_cluster([same_source_a, different_source])
    assert len(confirmed) == 1
    ev = confirmed[0]
    assert ev.sources_count == 2
    assert "Caixin" not in ev.all_sources  # all_sources holds URLs not source names
    assert len(ev.all_sources) == 2
    assert len(ev.content_hash) == 64


@pytest.mark.asyncio
async def test_cluster_with_llm_uses_mock_caller() -> None:
    events = [
        _ev("Caixin", "PBOC cuts RRR by 0.5 pct", "央行降准 0.5 个百分点"),
        _ev("Xinhua", "China central bank lowers reserve requirement"),
        _ev("Reuters", "Earnings beat at TSMC", "Unrelated", lang="en"),
    ]

    async def fake_llm(prompt: str) -> dict:
        assert "PBOC" in prompt
        return {
            "clusters": [
                {
                    "cluster_id": "rrr-cut",
                    "item_ids": [0, 1],
                    "primary_title": "PBOC cuts RRR by 0.5pct",
                    "summary": "Two outlets confirm the cut.",
                },
                # Single-source cluster should be filtered out by guardrail.
                {
                    "cluster_id": "tsmc",
                    "item_ids": [2],
                    "primary_title": "TSMC earnings",
                    "summary": "",
                },
            ]
        }

    confirmed = await cluster_with_llm(events, llm=fake_llm)
    assert len(confirmed) == 1
    only = confirmed[0]
    assert only.sources_count == 2
    assert only.primary_title == "PBOC cuts RRR by 0.5pct"
    assert "zh" in only.languages or "en" in only.languages


@pytest.mark.asyncio
async def test_cluster_with_llm_falls_back_on_error() -> None:
    events = [
        _ev("Caixin", "Shared keywords reserve requirement reduction"),
        _ev("Xinhua", "Shared keywords reserve requirement reduction"),
    ]

    async def boom(prompt: str) -> dict:
        raise RuntimeError("LLM unavailable")

    confirmed = await cluster_with_llm(events, llm=boom)
    assert len(confirmed) == 1
    assert confirmed[0].sources_count == 2


@pytest.mark.asyncio
async def test_cross_reference_end_to_end() -> None:
    events = [
        _ev("Caixin", "PBOC announces RRR cut 0.5 percentage points"),
        _ev("Xinhua", "PBOC cuts RRR by 0.5 percentage points"),
    ]

    async def fake_llm(prompt: str) -> dict:
        return {
            "clusters": [
                {
                    "cluster_id": "c0",
                    "item_ids": [0, 1],
                    "primary_title": "PBOC cuts RRR 0.5pct",
                    "summary": "Confirmed.",
                }
            ]
        }

    confirmed = await cross_reference(events, llm=fake_llm)
    assert len(confirmed) == 1
    ev = confirmed[0]
    assert ev.content_hash == content_hash("PBOC cuts RRR 0.5pct", [e.url for e in events])
