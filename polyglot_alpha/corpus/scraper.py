"""Scrape Polymarket Gamma API into a parquet corpus.

We hit the public ``events`` endpoint (rather than the bare ``markets``
endpoint) because events carry the ``category`` field and a ``tags`` list
that the per-market view does not expose. Each event in turn contains
one or more markets; we flatten the children into rows.

The output schema (parquet columns):

    market_id           str   - Polymarket condition id, primary key
    question            str   - the human-readable question title
    category            str   - free-form category, may be empty
    tags                str   - JSON-serialized list[str] of tag labels
    resolution_date     str   - ISO-8601, may be empty if endless
    resolution_criteria str   - text from market.description (may be long)
    event_id            str   - parent event id for grouping
    event_title         str   - parent event title (often "What will X by Y?")
    volume_usd          float - lifetime trade volume on Polymarket
    closed              bool  - whether the market has resolved

We deliberately skip multi-outcome arrays with more than two outcomes
because the framing-pattern analysis assumes binary YES/NO questions.

Rate limit handling: 429s trigger exponential backoff, and any partial
results so far are flushed to parquet on KeyboardInterrupt so a long
crawl can be resumed by re-running with a higher ``--start-offset``.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd
import requests

LOGGER = logging.getLogger(__name__)

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
DEFAULT_PAGE_SIZE = 100  # Gamma caps at ~500 but smaller pages are friendlier
DEFAULT_TARGET = 5000
DEFAULT_TIMEOUT_S = 30
MAX_BACKOFF_S = 60
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class CorpusRow:
    """One row in the parquet corpus."""

    market_id: str
    question: str
    category: str
    tags: str
    resolution_date: str
    resolution_criteria: str
    event_id: str
    event_title: str
    volume_usd: float
    closed: bool


# --------------------------------------------------------------------------- #
# HTTP layer.                                                                 #
# --------------------------------------------------------------------------- #


def _request_events_page(
    *,
    limit: int,
    offset: int,
    closed: Optional[bool],
    session: requests.Session,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_retries: int = 4,
) -> list[dict[str, Any]]:
    """Fetch a single page of events, retrying on transient failure."""

    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if closed is not None:
        params["closed"] = "true" if closed else "false"

    backoff_s = 1.0
    for attempt in range(max_retries):
        try:
            resp = session.get(
                GAMMA_EVENTS_URL, params=params, timeout=timeout_s
            )
        except requests.RequestException as e:
            LOGGER.warning("network error (attempt %d): %s", attempt + 1, e)
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, MAX_BACKOFF_S)
            continue

        if resp.status_code in RETRYABLE_STATUS:
            LOGGER.warning(
                "retryable status %d at offset=%d (attempt %d)",
                resp.status_code,
                offset,
                attempt + 1,
            )
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, MAX_BACKOFF_S)
            continue

        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise RuntimeError(
                f"Unexpected response shape at offset {offset}: "
                f"{type(data).__name__}"
            )
        return data

    raise RuntimeError(
        f"Gamma API kept failing after {max_retries} retries at offset {offset}"
    )


# --------------------------------------------------------------------------- #
# Normalization.                                                              #
# --------------------------------------------------------------------------- #


def _is_binary_market(market: dict[str, Any]) -> bool:
    """Return True if the market has exactly two outcomes (YES/NO style)."""

    raw = market.get("outcomes")
    if raw is None:
        # Most Gamma markets are binary even when outcomes is empty.
        return True
    try:
        outcomes = raw if isinstance(raw, list) else json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    return len(outcomes) == 2


def _event_tag_labels(event: dict[str, Any]) -> list[str]:
    tags = event.get("tags") or []
    out: list[str] = []
    for t in tags:
        if isinstance(t, dict):
            label = t.get("label")
            if isinstance(label, str) and label and label != "All":
                out.append(label)
    return out


def event_to_rows(event: dict[str, Any]) -> list[CorpusRow]:
    """Flatten one Gamma event into one CorpusRow per binary child market."""

    event_id = str(event.get("id", ""))
    event_title = event.get("title") or ""
    event_category = event.get("category") or ""
    tag_labels = _event_tag_labels(event)
    if not event_category and tag_labels:
        event_category = tag_labels[0]

    markets = event.get("markets") or []
    rows: list[CorpusRow] = []
    for market in markets:
        if not _is_binary_market(market):
            continue
        question = (market.get("question") or "").strip()
        if not question:
            continue
        market_id = str(market.get("id") or market.get("conditionId") or "")
        if not market_id:
            continue
        end_date = market.get("endDate") or market.get("umaEndDate") or ""
        try:
            volume = float(market.get("volume") or market.get("volumeNum") or 0)
        except (TypeError, ValueError):
            volume = 0.0
        rows.append(
            CorpusRow(
                market_id=market_id,
                question=question,
                category=event_category,
                tags=json.dumps(tag_labels),
                resolution_date=end_date,
                resolution_criteria=(market.get("description") or "").strip(),
                event_id=event_id,
                event_title=event_title,
                volume_usd=volume,
                closed=bool(market.get("closed", False)),
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# Crawl driver.                                                               #
# --------------------------------------------------------------------------- #


def scrape_polymarket(
    *,
    target_rows: int = DEFAULT_TARGET,
    page_size: int = DEFAULT_PAGE_SIZE,
    start_offset: int = 0,
    include_closed: bool = True,
    session: Optional[requests.Session] = None,
) -> list[CorpusRow]:
    """Crawl Gamma until ``target_rows`` binary questions are collected.

    The crawler issues two passes if ``include_closed`` is True — once for
    open markets and once for closed — because the Gamma endpoint sorts
    open markets first and otherwise we'd never reach historical depth.
    """

    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "polyglot-alpha-corpus/0.1")

    seen_ids: set[str] = set()
    rows: list[CorpusRow] = []

    for closed_flag in (False, True) if include_closed else (False,):
        offset = start_offset
        empty_pages_in_a_row = 0
        while len(rows) < target_rows:
            page = _request_events_page(
                limit=page_size,
                offset=offset,
                closed=closed_flag,
                session=session,
            )
            if not page:
                empty_pages_in_a_row += 1
                if empty_pages_in_a_row >= 2:
                    break
                offset += page_size
                continue
            empty_pages_in_a_row = 0

            new_in_page = 0
            for event in page:
                for row in event_to_rows(event):
                    if row.market_id in seen_ids:
                        continue
                    seen_ids.add(row.market_id)
                    rows.append(row)
                    new_in_page += 1
                    if len(rows) >= target_rows:
                        break
                if len(rows) >= target_rows:
                    break

            LOGGER.info(
                "offset=%d closed=%s page_rows=%d total=%d",
                offset,
                closed_flag,
                new_in_page,
                len(rows),
            )
            offset += page_size

    return rows


def rows_to_dataframe(rows: Iterable[CorpusRow]) -> pd.DataFrame:
    return pd.DataFrame([asdict(r) for r in rows])


def save_parquet(rows: Iterable[CorpusRow], dest: Path) -> Path:
    """Write rows to a parquet file (overwriting if it exists)."""

    df = rows_to_dataframe(rows)
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dest, index=False)
    return dest


# --------------------------------------------------------------------------- #
# CLI.                                                                        #
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="corpus/polymarket_questions.parquet",
        help="Destination parquet path",
    )
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument(
        "--open-only",
        action="store_true",
        help="Skip closed markets (faster, fewer rows)",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING")
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        rows = scrape_polymarket(
            target_rows=args.target,
            page_size=args.page_size,
            start_offset=args.start_offset,
            include_closed=not args.open_only,
        )
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted; saving partial corpus")
        rows = []
    out = save_parquet(rows, Path(args.out))
    LOGGER.info("wrote %d rows -> %s", len(rows), out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
