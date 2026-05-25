"""Cross-reference clustering.

Given a list of :class:`RawEvent` items, ask an LLM to cluster them by topic
and emit :class:`ConfirmedEvent` entries only when at least two **distinct**
sources confirm the same topic.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Iterable, Optional

from polyglot_alpha.ingestion.models import ConfirmedEvent, RawEvent
from polyglot_alpha.llm import complete_json

LOGGER = logging.getLogger(__name__)

DEFAULT_WINDOW = timedelta(hours=1)
MIN_SOURCES = 2

CLUSTER_PROMPT_HEADER = (
    "You are an event-correlation engine for a multilingual news watcher.\n"
    "You receive a JSON list of recent news items from multiple sources and\n"
    "languages. Your job: cluster items that report the SAME real-world event.\n"
    "\n"
    "Rules:\n"
    "  * Two items belong to the same cluster only if they describe the same\n"
    "    concrete event (e.g., the same policy announcement, the same\n"
    "    incident, the same earnings release).\n"
    "  * Do NOT cluster items that merely share a topic (e.g., 'inflation').\n"
    "  * Return STRICT JSON of shape:\n"
    "      {\"clusters\":[{\"cluster_id\":\"c0\",\"item_ids\":[0,3,5],\n"
    "                       \"primary_title\":\"...\",\"summary\":\"...\"}]}\n"
    "  * 'item_ids' refer to the indices in the input list.\n"
    "  * Omit any cluster that contains fewer than two distinct sources.\n"
)


# --------------------------------------------------------------------------- #
# Helpers.                                                                    #
# --------------------------------------------------------------------------- #


def filter_recent(
    events: Iterable[RawEvent], window: timedelta = DEFAULT_WINDOW
) -> list[RawEvent]:
    """Keep only events whose ``published_at`` is within ``window`` of now."""

    now = datetime.now(tz=timezone.utc)
    out: list[RawEvent] = []
    for ev in events:
        ts = ev.published_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if now - ts <= window:
            out.append(ev)
    return out


def content_hash(canonical_title: str, source_urls: Iterable[str]) -> str:
    """Deterministic sha256 over canonical title + sorted source URLs."""

    sorted_urls = sorted({u.strip() for u in source_urls if u})
    payload = canonical_title.strip() + "\n" + "\n".join(sorted_urls)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _events_payload(events: list[RawEvent]) -> list[dict]:
    return [
        {
            "id": idx,
            "source": ev.source,
            "language": ev.language,
            "title": ev.title,
            "summary": ev.summary[:300],
            "url": ev.url,
        }
        for idx, ev in enumerate(events)
    ]


def _make_confirmed(
    cluster_id: str,
    indices: list[int],
    events: list[RawEvent],
    primary_title: str,
    summary: str,
) -> Optional[ConfirmedEvent]:
    """Build a :class:`ConfirmedEvent` if cluster meets the >=2 source rule."""

    valid = [events[i] for i in indices if 0 <= i < len(events)]
    if not valid:
        return None
    distinct_sources = {ev.source for ev in valid}
    if len(distinct_sources) < MIN_SOURCES:
        return None
    urls = sorted({ev.url for ev in valid})
    languages = sorted({ev.language for ev in valid})
    title = primary_title.strip() or valid[0].title
    return ConfirmedEvent(
        cluster_id=cluster_id,
        sources_count=len(distinct_sources),
        primary_title=title,
        all_sources=urls,
        content_hash=content_hash(title, urls),
        languages=languages,
        summary=summary.strip(),
    )


# --------------------------------------------------------------------------- #
# Heuristic fallback clusterer (used in tests / when LLM is unavailable).     #
# --------------------------------------------------------------------------- #


_TOKEN_RE = re.compile(r"[\w一-鿿]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 2}


def heuristic_cluster(events: list[RawEvent]) -> list[ConfirmedEvent]:
    """A lightweight token-overlap clusterer.

    Two events are merged when they share >=3 long tokens. Useful for tests
    and as a deterministic fallback when no LLM key is configured.
    """

    parents = list(range(len(events)))

    def find(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parents[ri] = rj

    tokens = [_tokens(f"{ev.title} {ev.summary}") for ev in events]
    for i in range(len(events)):
        for j in range(i + 1, len(events)):
            if len(tokens[i] & tokens[j]) >= 3:
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for idx in range(len(events)):
        clusters.setdefault(find(idx), []).append(idx)

    confirmed: list[ConfirmedEvent] = []
    for cid, indices in clusters.items():
        member_sources = {events[i].source for i in indices}
        if len(member_sources) < MIN_SOURCES:
            continue
        primary = events[indices[0]].title
        summary = events[indices[0]].summary
        ev = _make_confirmed(
            f"c{cid}", indices, events, primary_title=primary, summary=summary
        )
        if ev is not None:
            confirmed.append(ev)
    return confirmed


# --------------------------------------------------------------------------- #
# LLM-backed clusterer.                                                       #
# --------------------------------------------------------------------------- #


LLMCaller = Callable[[str], Awaitable[dict]]


async def cluster_with_llm(
    events: list[RawEvent],
    *,
    llm: LLMCaller | None = None,
) -> list[ConfirmedEvent]:
    """Ask the LLM to cluster events and return confirmed ones."""

    if not events:
        return []

    payload = _events_payload(events)
    prompt = CLUSTER_PROMPT_HEADER + "\nInput items:\n" + _json_dumps(payload)
    caller: LLMCaller = llm or (lambda p: complete_json(p))
    try:
        data = await caller(prompt)
    except Exception as exc:
        LOGGER.warning("LLM clustering failed (%s); falling back to heuristic.", exc)
        return heuristic_cluster(events)

    clusters = (data or {}).get("clusters", [])
    confirmed: list[ConfirmedEvent] = []
    for cluster in clusters:
        cluster_id = str(cluster.get("cluster_id") or f"c{len(confirmed)}")
        indices = [int(i) for i in cluster.get("item_ids", []) if isinstance(i, (int, str))]
        primary_title = str(cluster.get("primary_title") or "")
        summary = str(cluster.get("summary") or "")
        ev = _make_confirmed(cluster_id, indices, events, primary_title, summary)
        if ev is not None:
            confirmed.append(ev)
    return confirmed


def _json_dumps(payload) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


async def cross_reference(
    events: Iterable[RawEvent],
    *,
    window: timedelta = DEFAULT_WINDOW,
    llm: LLMCaller | None = None,
) -> list[ConfirmedEvent]:
    """Public entry point: filter recent + cluster + emit confirmed events."""

    recent = filter_recent(list(events), window=window)
    if len(recent) < MIN_SOURCES:
        return []
    return await cluster_with_llm(recent, llm=llm)
