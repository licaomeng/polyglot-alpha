"""RSS aggregator tests (mocked feeds, in-memory SQLite)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from polyglot_alpha.ingestion import rss_aggregator
from polyglot_alpha.ingestion.models import RawEntry, Source, get_engine
from polyglot_alpha.ingestion.rss_aggregator import (
    RSSAggregator,
    filter_new,
    load_sources,
    parse_feed,
)
from sqlmodel import Session, select

CAIXIN_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Caixin</title>
    <item>
      <title>央行宣布降准 0.5 个百分点</title>
      <link>https://www.caixinglobal.com/2026/05/25/pboc-rrr-cut</link>
      <guid>caixin-rrr-cut-2026-05-25</guid>
      <description>People's Bank of China cuts the reserve requirement ratio by 0.5 percentage points.</description>
      <pubDate>Mon, 25 May 2026 09:00:00 +0800</pubDate>
    </item>
    <item>
      <title>中国 4 月制造业 PMI 回升至 51.2</title>
      <link>https://www.caixinglobal.com/2026/05/01/pmi-april</link>
      <guid>caixin-pmi-2026-04</guid>
      <description>April manufacturing PMI rebounds to 51.2.</description>
      <pubDate>Sun, 01 May 2026 08:30:00 +0800</pubDate>
    </item>
  </channel>
</rss>
"""

XINHUA_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Xinhua</title>
    <item>
      <title>PBOC announces 0.5pct cut to RRR effective immediately</title>
      <link>https://xinhuanet.com/2026/05/25/pboc-rrr</link>
      <guid>xinhua-rrr-2026-05-25</guid>
      <description>China's central bank lowers the reserve requirement ratio.</description>
      <pubDate>Mon, 25 May 2026 09:30:00 +0800</pubDate>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def engine(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    return get_engine(db_url)


@pytest.fixture
def mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if "caixin" in str(request.url):
            return httpx.Response(200, text=CAIXIN_FEED)
        if "xinhuanet" in str(request.url) or "xinhua" in str(request.url):
            return httpx.Response(200, text=XINHUA_FEED)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


def test_load_sources_has_eight_distinct_languages() -> None:
    """sources.json must list all eight required feeds."""

    sources = load_sources()
    assert len(sources) >= 8
    names = {s["name"] for s in sources}
    assert {"Caixin", "Xinhua", "SCMP", "Asahi Shimbun", "Le Monde", "Deutsche Welle"}.issubset(
        names
    )
    for src in sources:
        assert "url" in src and src["url"].startswith("http")
        assert src.get("fetch_interval_seconds", 300) >= 60


def test_parse_feed_extracts_raw_events() -> None:
    source = {"name": "Caixin", "url": "https://www.caixinglobal.com/rss/news.xml", "language": "zh"}
    events = parse_feed(source, CAIXIN_FEED)
    assert len(events) == 2
    first = events[0]
    assert "降准" in first.title
    assert first.source == "Caixin"
    assert first.language == "zh"
    assert first.url.startswith("https://www.caixinglobal.com")


def test_filter_new_deduplicates(engine) -> None:
    source = {"name": "Caixin", "url": "https://test.local/feed", "language": "zh"}
    events = parse_feed(source, CAIXIN_FEED)
    entry_ids = [e.url for e in events]

    first_pass = filter_new(engine, source["url"], events, entry_ids)
    assert len(first_pass) == 2

    second_pass = filter_new(engine, source["url"], events, entry_ids)
    assert second_pass == []

    with Session(engine) as session:
        rows = session.exec(select(RawEntry)).all()
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_poll_once_returns_new_events(engine, mock_transport) -> None:
    sources = [
        {
            "name": "Caixin",
            "url": "https://www.caixinglobal.com/rss/news.xml",
            "language": "zh",
        },
        {
            "name": "Xinhua",
            "url": "https://xinhuanet.com/rss.xml",
            "language": "zh",
        },
    ]
    async with httpx.AsyncClient(transport=mock_transport) as client:
        aggregator = RSSAggregator(
            sources=sources, engine=engine, http_client=client
        )
        first = await aggregator.poll_once()
        assert len(first) == 3  # 2 caixin + 1 xinhua

        second = await aggregator.poll_once()
        assert second == []  # dedup

    with Session(engine) as session:
        registered = session.exec(select(Source)).all()
        assert {s.name for s in registered} == {"Caixin", "Xinhua"}


@pytest.mark.asyncio
async def test_poll_once_handles_fetch_error(engine, mock_transport) -> None:
    sources = [
        {"name": "Broken", "url": "https://does-not-exist.local/feed", "language": "en"},
        {"name": "Caixin", "url": "https://www.caixinglobal.com/rss/news.xml", "language": "zh"},
    ]
    async with httpx.AsyncClient(transport=mock_transport) as client:
        aggregator = RSSAggregator(
            sources=sources, engine=engine, http_client=client
        )
        events = await aggregator.poll_once()
    assert all(ev.source == "Caixin" for ev in events)
    assert len(events) == 2
