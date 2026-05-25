"""Asynchronous RSS aggregator.

Polls a configurable list of RSS feeds, deduplicates entries by
``(source_url, entry_id)`` in SQLite, and yields :class:`RawEvent` instances
for downstream cross-reference.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from time import mktime
from typing import Any, Iterable, Optional

import feedparser
import httpx
from sqlmodel import Session, select

from polyglot_alpha.ingestion.models import (
    RawEntry,
    RawEvent,
    Source,
    get_engine,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_SOURCES_PATH = Path(__file__).with_name("sources.json")
DEFAULT_FETCH_INTERVAL_SECONDS = 300
DEFAULT_USER_AGENT = "PolyglotAlpha/0.2 (+https://polyglot-alpha.local)"
DEFAULT_HTTP_TIMEOUT = 15.0


# --------------------------------------------------------------------------- #
# Source registry I/O.                                                        #
# --------------------------------------------------------------------------- #


def load_sources(path: Path = DEFAULT_SOURCES_PATH) -> list[dict[str, Any]]:
    """Load the source registry JSON file."""

    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError(f"sources.json malformed: 'sources' must be a list")
    return sources


# --------------------------------------------------------------------------- #
# Feed parsing.                                                               #
# --------------------------------------------------------------------------- #


def _entry_id(entry: Any, fallback_url: str) -> str:
    return (
        getattr(entry, "id", None)
        or getattr(entry, "guid", None)
        or getattr(entry, "link", None)
        or fallback_url
    )


def _entry_published(entry: Any) -> datetime:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        struct = getattr(entry, attr, None)
        if struct is not None:
            try:
                return datetime.fromtimestamp(mktime(struct), tz=timezone.utc)
            except Exception:  # pragma: no cover - degenerate timestamps
                continue
    return datetime.now(tz=timezone.utc)


def parse_feed(
    source: dict[str, Any],
    body: bytes | str,
) -> list[RawEvent]:
    """Parse a raw feed body into :class:`RawEvent` instances."""

    parsed = feedparser.parse(body)
    out: list[RawEvent] = []
    for entry in parsed.entries:
        link = getattr(entry, "link", None) or source["url"]
        title = (getattr(entry, "title", "") or "").strip()
        summary = (getattr(entry, "summary", "") or getattr(entry, "description", "") or "").strip()
        if not title:
            continue
        out.append(
            RawEvent(
                source=source["name"],
                title=title,
                summary=summary,
                url=link,
                published_at=_entry_published(entry),
                language=source.get("language", "unknown"),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Async fetch.                                                                #
# --------------------------------------------------------------------------- #


async def fetch_feed(
    client: httpx.AsyncClient,
    source: dict[str, Any],
) -> Optional[str]:
    """Fetch a single feed body. Returns ``None`` on failure."""

    try:
        resp = await client.get(source["url"])
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        LOGGER.warning("Fetch failed for %s: %s", source["name"], exc)
        return None


# --------------------------------------------------------------------------- #
# Dedup.                                                                      #
# --------------------------------------------------------------------------- #


def filter_new(
    engine,
    source_url: str,
    events: Iterable[RawEvent],
    entry_ids: Iterable[str],
) -> list[RawEvent]:
    """Persist seen entry ids and return only the brand-new events."""

    events_list = list(events)
    entry_ids_list = list(entry_ids)
    if len(events_list) != len(entry_ids_list):
        raise ValueError("events and entry_ids must have the same length")

    fresh: list[RawEvent] = []
    with Session(engine) as session:
        existing = {
            row.entry_id
            for row in session.exec(
                select(RawEntry).where(RawEntry.source_url == source_url)
            ).all()
        }
        for event, entry_id in zip(events_list, entry_ids_list):
            if entry_id in existing:
                continue
            fresh.append(event)
            session.add(RawEntry(source_url=source_url, entry_id=entry_id))
            existing.add(entry_id)
        session.commit()
    return fresh


# --------------------------------------------------------------------------- #
# Aggregator.                                                                 #
# --------------------------------------------------------------------------- #


class RSSAggregator:
    """Poll a list of RSS sources and emit deduplicated :class:`RawEvent`s."""

    def __init__(
        self,
        sources: list[dict[str, Any]] | None = None,
        *,
        engine: Any | None = None,
        http_client: httpx.AsyncClient | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        self.sources = sources if sources is not None else load_sources()
        self.engine = engine or get_engine()
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
        )
        self._register_sources()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "RSSAggregator":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def _register_sources(self) -> None:
        with Session(self.engine) as session:
            for src in self.sources:
                existing = session.exec(
                    select(Source).where(Source.url == src["url"])
                ).first()
                if existing is None:
                    session.add(
                        Source(
                            name=src["name"],
                            url=src["url"],
                            language=src.get("language", "unknown"),
                        )
                    )
            session.commit()

    async def poll_once(self) -> list[RawEvent]:
        """Single pass over every source. Returns newly-seen events."""

        results = await asyncio.gather(
            *(self._poll_source(src) for src in self.sources),
            return_exceptions=True,
        )
        flattened: list[RawEvent] = []
        for res in results:
            if isinstance(res, Exception):
                LOGGER.warning("Source polling raised: %s", res)
                continue
            flattened.extend(res)
        return flattened

    async def _poll_source(self, source: dict[str, Any]) -> list[RawEvent]:
        body = await fetch_feed(self._client, source)
        if body is None:
            return []
        parsed = feedparser.parse(body)
        events: list[RawEvent] = []
        entry_ids: list[str] = []
        for entry in parsed.entries:
            title = (getattr(entry, "title", "") or "").strip()
            if not title:
                continue
            link = getattr(entry, "link", None) or source["url"]
            events.append(
                RawEvent(
                    source=source["name"],
                    title=title,
                    summary=(
                        getattr(entry, "summary", "")
                        or getattr(entry, "description", "")
                        or ""
                    ).strip(),
                    url=link,
                    published_at=_entry_published(entry),
                    language=source.get("language", "unknown"),
                )
            )
            entry_ids.append(_entry_id(entry, link))

        fresh = filter_new(self.engine, source["url"], events, entry_ids)
        with Session(self.engine) as session:
            row = session.exec(
                select(Source).where(Source.url == source["url"])
            ).first()
            if row is not None:
                row.last_fetched = datetime.now(tz=timezone.utc).replace(tzinfo=None)
                session.add(row)
                session.commit()
        return fresh

    async def run_forever(
        self,
        on_batch,
        interval_seconds: int = DEFAULT_FETCH_INTERVAL_SECONDS,
    ) -> None:
        """Run an infinite poll loop calling ``on_batch(list[RawEvent])``."""

        while True:
            batch = await self.poll_once()
            if batch:
                await on_batch(batch)
            await asyncio.sleep(interval_seconds)
