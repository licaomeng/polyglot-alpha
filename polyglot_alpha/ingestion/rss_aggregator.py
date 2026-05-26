"""Asynchronous RSS aggregator.

Polls a configurable list of RSS feeds, deduplicates entries by
``(source_url, entry_id)`` in SQLite, and yields :class:`RawEvent` instances
for downstream cross-reference.

W13-C (2026-05-26): per-source structured logging + fail-loud behaviour.
Every fetch emits an ``rss.fetch`` INFO line carrying ``source``, ``url``,
``status``, ``entries``, ``latency_ms`` and a verdict. The end of every run
emits an ``rss.fetch_summary`` line and (when called via
:func:`poll_sources_once_with_report`) returns a :class:`RSSHealthReport`
that the trigger route persists onto the event so the UI can show
"live mode: only N/M sources reachable". If the healthy count falls below
``RSS_REQUIRE_AT_LEAST`` (env, default 2) the aggregator raises
:class:`RSSAggregatorError` instead of silently returning an empty list —
that error is surfaced to operators rather than swallowed by a fixture
fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
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

# Env-controlled fail-loud threshold. The aggregator raises
# :class:`RSSAggregatorError` when fewer than this many sources came back
# healthy. Operators can lower the bar to ``1`` for single-source dev
# scenarios, or raise it for stricter prod / e2e expectations.
RSS_REQUIRE_AT_LEAST_ENV = "RSS_REQUIRE_AT_LEAST"
DEFAULT_MIN_HEALTHY_SOURCES = 2


# --------------------------------------------------------------------------- #
# Exceptions / health-report dataclasses.                                     #
# --------------------------------------------------------------------------- #


class RSSAggregatorError(RuntimeError):
    """Raised when the aggregator cannot satisfy the minimum-healthy bar.

    Carries a populated :class:`RSSHealthReport` so callers (e.g. the
    trigger route) can surface the per-source breakdown into the event
    record / SSE payload instead of returning an opaque 500.
    """

    def __init__(self, message: str, report: "RSSHealthReport") -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class RSSFetchResult:
    """Per-source outcome of a single fetch attempt."""

    name: str
    url: str
    language: str
    status_code: int | None
    latency_ms: int
    entries: int
    verdict: str  # OK | BAD_STATUS | TIMEOUT | CONN_ERR | PARSE_ERR | DISABLED
    error: str | None = None

    @property
    def healthy(self) -> bool:
        return self.verdict == "OK" and self.entries > 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RSSHealthReport:
    """Aggregated per-run health summary, attached to the event payload."""

    total_sources: int = 0
    healthy: int = 0
    broken: int = 0
    disabled: int = 0
    entries_collected: int = 0
    min_required: int = DEFAULT_MIN_HEALTHY_SOURCES
    degraded: bool = False
    results: list[RSSFetchResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_sources": self.total_sources,
            "healthy": self.healthy,
            "broken": self.broken,
            "disabled": self.disabled,
            "entries_collected": self.entries_collected,
            "min_required": self.min_required,
            "degraded": self.degraded,
            "results": [r.to_dict() for r in self.results],
        }


# --------------------------------------------------------------------------- #
# Source registry I/O.                                                        #
# --------------------------------------------------------------------------- #


def load_sources(path: Path = DEFAULT_SOURCES_PATH) -> list[dict[str, Any]]:
    """Load the source registry JSON file.

    Returns every entry, including ``enabled: false`` ones, so callers can
    decide whether to skip them (and surface ``DISABLED`` rows in the
    health report). Downstream consumers that just want the active list
    should use :func:`enabled_sources`.
    """

    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError(f"sources.json malformed: 'sources' must be a list")
    return sources


def enabled_sources(
    sources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return only sources whose ``enabled`` flag is truthy (default True)."""

    base = sources if sources is not None else load_sources()
    return [s for s in base if s.get("enabled", True)]


def _min_required() -> int:
    """Resolve the ``RSS_REQUIRE_AT_LEAST`` env value (clamped to >=1)."""

    raw = os.environ.get(RSS_REQUIRE_AT_LEAST_ENV)
    if not raw:
        return DEFAULT_MIN_HEALTHY_SOURCES
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        LOGGER.warning(
            "rss.config invalid %s=%r — falling back to %d",
            RSS_REQUIRE_AT_LEAST_ENV,
            raw,
            DEFAULT_MIN_HEALTHY_SOURCES,
        )
        return DEFAULT_MIN_HEALTHY_SOURCES


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
    """Fetch a single feed body. Returns ``None`` on failure.

    Kept for backwards compatibility — emits the legacy WARN line so
    callers that don't go through :func:`fetch_feed_observed` still see
    the failure in logs.
    """

    result, body = await fetch_feed_observed(client, source)
    if not result.healthy or body is None:
        return None
    return body


async def fetch_feed_observed(
    client: httpx.AsyncClient,
    source: dict[str, Any],
) -> tuple[RSSFetchResult, str | None]:
    """Fetch one source and emit a structured ``rss.fetch`` log line.

    Returns ``(result, body_text)`` where ``body_text`` is ``None`` unless
    the fetch succeeded with a 2xx status. The :class:`RSSFetchResult`
    always reflects the real outcome (status code, latency, verdict)
    regardless of success.
    """

    name = str(source.get("name", "<unnamed>"))
    url = str(source.get("url", ""))
    language = str(source.get("language", "unknown"))

    started = time.perf_counter()
    try:
        resp = await client.get(url)
    except httpx.TimeoutException as exc:
        latency = int((time.perf_counter() - started) * 1000)
        LOGGER.warning(
            "rss.fetch source=%s url=%s TIMEOUT latency_ms=%d SKIPPED",
            name,
            url,
            latency,
        )
        return (
            RSSFetchResult(
                name=name,
                url=url,
                language=language,
                status_code=None,
                latency_ms=latency,
                entries=0,
                verdict="TIMEOUT",
                error=str(exc) or "request timed out",
            ),
            None,
        )
    except httpx.HTTPError as exc:
        latency = int((time.perf_counter() - started) * 1000)
        LOGGER.warning(
            "rss.fetch source=%s url=%s CONN_ERR latency_ms=%d err=%r SKIPPED",
            name,
            url,
            latency,
            str(exc),
        )
        return (
            RSSFetchResult(
                name=name,
                url=url,
                language=language,
                status_code=None,
                latency_ms=latency,
                entries=0,
                verdict="CONN_ERR",
                error=str(exc),
            ),
            None,
        )
    latency = int((time.perf_counter() - started) * 1000)

    if resp.status_code >= 400:
        LOGGER.warning(
            "rss.fetch source=%s url=%s status=%d latency_ms=%d SKIPPED",
            name,
            url,
            resp.status_code,
            latency,
        )
        return (
            RSSFetchResult(
                name=name,
                url=url,
                language=language,
                status_code=resp.status_code,
                latency_ms=latency,
                entries=0,
                verdict="BAD_STATUS",
                error=f"HTTP {resp.status_code}",
            ),
            None,
        )

    body_text = resp.text
    try:
        parsed = feedparser.parse(resp.content)
        entries = len(getattr(parsed, "entries", []) or [])
    except Exception as exc:  # pragma: no cover - feedparser robust
        LOGGER.warning(
            "rss.fetch source=%s url=%s status=%d PARSE_ERR err=%r SKIPPED",
            name,
            url,
            resp.status_code,
            exc,
        )
        return (
            RSSFetchResult(
                name=name,
                url=url,
                language=language,
                status_code=resp.status_code,
                latency_ms=latency,
                entries=0,
                verdict="PARSE_ERR",
                error=str(exc),
            ),
            None,
        )

    verdict = "OK" if entries > 0 else "PARSE_ERR"
    log_level = logging.INFO if verdict == "OK" else logging.WARNING
    LOGGER.log(
        log_level,
        "rss.fetch source=%s url=%s status=%d entries=%d latency_ms=%d verdict=%s",
        name,
        url,
        resp.status_code,
        entries,
        latency,
        verdict,
    )
    return (
        RSSFetchResult(
            name=name,
            url=url,
            language=language,
            status_code=resp.status_code,
            latency_ms=latency,
            entries=entries,
            verdict=verdict,
            error=None if verdict == "OK" else "feed parsed to zero entries",
        ),
        body_text if verdict == "OK" else None,
    )


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


# --------------------------------------------------------------------------- #
# Convenience: one-shot poll for trigger endpoints.                           #
# --------------------------------------------------------------------------- #


async def poll_sources_once(
    sources: list[dict[str, Any]] | None = None,
    *,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> list[RawEvent]:
    """Backwards-compatible one-shot poll returning only the events list.

    Internally delegates to :func:`poll_sources_once_with_report` and
    discards the health report. New callers that want the per-source
    breakdown (and the fail-loud behaviour) should use the ``_with_report``
    variant directly.
    """

    events, _report = await poll_sources_once_with_report(
        sources, timeout=timeout, require_at_least=None
    )
    return events


async def poll_sources_once_with_report(
    sources: list[dict[str, Any]] | None = None,
    *,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    require_at_least: int | None = None,
) -> tuple[list[RawEvent], RSSHealthReport]:
    """One-shot poll that returns both events AND a :class:`RSSHealthReport`.

    Per-source ``rss.fetch`` lines are emitted by
    :func:`fetch_feed_observed`. After all sources complete, this function
    emits a single ``rss.fetch_summary`` INFO line and raises
    :class:`RSSAggregatorError` when ``healthy < require_at_least``
    (so the trigger handler can mark the event FAILED rather than
    silently producing an empty list).

    ``require_at_least=None`` resolves from the ``RSS_REQUIRE_AT_LEAST``
    env var (default 2). Pass an explicit integer (or ``0`` to disable
    the check) to override.
    """

    all_sources = sources if sources is not None else load_sources()
    active: list[dict[str, Any]] = []
    disabled_results: list[RSSFetchResult] = []
    for src in all_sources:
        if src.get("enabled", True):
            active.append(src)
            continue
        disabled_results.append(
            RSSFetchResult(
                name=str(src.get("name", "<unnamed>")),
                url=str(src.get("url", "")),
                language=str(src.get("language", "unknown")),
                status_code=None,
                latency_ms=0,
                entries=0,
                verdict="DISABLED",
                error=str(src.get("disabled_reason") or "marked enabled=false"),
            )
        )
        LOGGER.info(
            "rss.fetch source=%s url=%s DISABLED reason=%r SKIPPED",
            src.get("name", "<unnamed>"),
            src.get("url", ""),
            src.get("disabled_reason"),
        )

    started = time.perf_counter()
    fetch_results: list[RSSFetchResult] = []
    bodies: list[str | None] = []
    if active:
        async with httpx.AsyncClient(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            raw = await asyncio.gather(
                *(fetch_feed_observed(client, src) for src in active),
                return_exceptions=True,
            )
        for src, item in zip(active, raw):
            if isinstance(item, Exception):
                # ``fetch_feed_observed`` already swallows its own
                # exceptions and returns a result; this branch only fires
                # if something even more pathological happened.
                LOGGER.error(
                    "rss.fetch source=%s url=%s UNEXPECTED err=%r SKIPPED",
                    src.get("name"),
                    src.get("url"),
                    item,
                )
                fetch_results.append(
                    RSSFetchResult(
                        name=str(src.get("name", "<unnamed>")),
                        url=str(src.get("url", "")),
                        language=str(src.get("language", "unknown")),
                        status_code=None,
                        latency_ms=0,
                        entries=0,
                        verdict="CONN_ERR",
                        error=str(item),
                    )
                )
                bodies.append(None)
                continue
            result, body = item
            fetch_results.append(result)
            bodies.append(body)
    total_ms = int((time.perf_counter() - started) * 1000)

    # Parse all healthy bodies into RawEvents.
    out: list[RawEvent] = []
    for src, body, result in zip(active, bodies, fetch_results):
        if not result.healthy or body is None:
            continue
        try:
            out.extend(parse_feed(src, body))
        except Exception as exc:  # pragma: no cover - feedparser robust
            LOGGER.warning(
                "rss.fetch source=%s url=%s post-parse_feed failed err=%r",
                src.get("name"),
                src.get("url"),
                exc,
            )

    combined_results = disabled_results + fetch_results
    healthy_count = sum(1 for r in fetch_results if r.healthy)
    broken_count = sum(
        1 for r in fetch_results if not r.healthy and r.verdict != "DISABLED"
    )
    disabled_count = len(disabled_results)
    threshold = _min_required() if require_at_least is None else require_at_least

    # Degraded == fewer healthy sources than we expected. "Expected" =
    # the number of active sources (i.e. those that weren't disabled).
    # Falling below ``min_required`` is captured by the raise below; we
    # still flag any per-source failure so the UI can show the banner.
    report = RSSHealthReport(
        total_sources=len(all_sources),
        healthy=healthy_count,
        broken=broken_count,
        disabled=disabled_count,
        entries_collected=len(out),
        min_required=threshold,
        degraded=(broken_count > 0) or (healthy_count < threshold),
        results=combined_results,
    )

    LOGGER.info(
        "rss.fetch_summary total_sources=%d active=%d disabled=%d healthy=%d "
        "broken=%d entries_collected=%d total_latency_ms=%d min_required=%d "
        "degraded=%s",
        report.total_sources,
        len(active),
        report.disabled,
        report.healthy,
        report.broken,
        report.entries_collected,
        total_ms,
        report.min_required,
        report.degraded,
    )

    if threshold > 0 and healthy_count < threshold:
        broken_names = [
            f"{r.name}({r.verdict})"
            for r in combined_results
            if not r.healthy and r.verdict != "DISABLED"
        ]
        raise RSSAggregatorError(
            (
                f"rss_all_unreachable: only {healthy_count}/{len(active)} active "
                f"sources healthy (min required={threshold}); broken="
                f"[{', '.join(broken_names)}]"
            ),
            report,
        )

    return out, report
