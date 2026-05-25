"""Pull the FULL Polymarket Gamma corpus into parquet/jsonl/csv.

Unlike :mod:`polyglot_alpha.corpus.scraper`, this script does not cap rows
at a target count and does not filter to binary YES/NO markets. We pull
every market the public Gamma API will return (open + closed, all
categories, all time), preserve EVERY field returned so downstream
consumers never need to re-scrape, and persist incrementally to parquet
so a long crawl can be resumed.

Pagination notes (validated 2026-05-25):
    * ``/markets?limit=500&offset=N`` caps at offset 10000 (validation
      error beyond that).
    * ``closed=true`` and ``closed=false`` return disjoint slices, so we
      paginate each.
    * ``start_date_min`` / ``start_date_max`` narrow further to escape the
      10k-offset wall — we slice by half-year windows.
    * ``/markets`` does NOT embed ``tags`` or ``category``; those live on
      ``/events``. We do a second crawl of ``/events`` to build an
      ``event_id -> {category, tags}`` lookup and join it in.

Output (under ``corpus/full/``):
    * polymarket_all_markets.parquet  (primary, ~tens of MB)
    * polymarket_all_markets.jsonl    (streaming consumers)
    * polymarket_all_markets.csv      (Excel-openable)
    * polymarket_all_markets_sample.csv (first 1000 rows, git-committable)
    * full_summary.md
    * distribution_by_{state,category,year}.json
    * volume_distribution.json
    * api_fields.json (discovery output)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd
import requests

LOGGER = logging.getLogger("polymarket-full-scraper")

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"

# Validated empirically — Gamma rejects offsets above 10000, and silently
# caps the per-page ``limit`` at 100, so we always request exactly 100.
GAMMA_OFFSET_CEILING = 10_000
DEFAULT_PAGE_SIZE = 100
DEFAULT_TIMEOUT_S = 30
MAX_BACKOFF_S = 60.0
INITIAL_BACKOFF_S = 2.0
MAX_RETRIES = 5
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
PROGRESS_EVERY = 1000
PERSIST_EVERY = 5000
HARD_CAP = 100_000

# Half-year windows from 2022 through 2027 — wide enough to capture any
# foreseeable corpus extension.
DATE_WINDOWS: list[tuple[Optional[str], Optional[str]]] = [
    ("2022-01-01", "2022-07-01"),
    ("2022-07-01", "2023-01-01"),
    ("2023-01-01", "2023-07-01"),
    ("2023-07-01", "2024-01-01"),
    ("2024-01-01", "2024-04-01"),
    ("2024-04-01", "2024-07-01"),
    ("2024-07-01", "2024-10-01"),
    ("2024-10-01", "2025-01-01"),
    ("2025-01-01", "2025-04-01"),
    ("2025-04-01", "2025-07-01"),
    ("2025-07-01", "2025-10-01"),
    ("2025-10-01", "2026-01-01"),
    ("2026-01-01", "2026-04-01"),
    ("2026-04-01", "2026-07-01"),
    ("2026-07-01", "2027-01-01"),
    # A safety-net pass with no window so we catch anything outside the
    # explicit windows (markets without a start_date in particular).
    (None, None),
]


# --------------------------------------------------------------------------- #
# HTTP layer with exponential backoff.                                        #
# --------------------------------------------------------------------------- #


def _request_page(
    url: str,
    params: dict[str, Any],
    session: requests.Session,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """GET one page; return [] for the documented offset-ceiling error.

    Any other validation/HTTP error is re-raised after ``MAX_RETRIES``.
    """

    backoff_s = INITIAL_BACKOFF_S
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout_s)
        except requests.RequestException as e:
            last_exc = e
            LOGGER.warning(
                "network error (attempt %d/%d): %s", attempt, MAX_RETRIES, e
            )
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, MAX_BACKOFF_S)
            continue

        if resp.status_code in RETRYABLE_STATUS:
            LOGGER.warning(
                "retryable status %d (attempt %d/%d) params=%s",
                resp.status_code,
                attempt,
                MAX_RETRIES,
                params,
            )
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, MAX_BACKOFF_S)
            continue

        # Successful or non-retryable; parse.
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
            except ValueError:
                err_body = resp.text
            # Gamma signals "out of range" with HTTP 400 + an error dict;
            # treat that as a soft EOF.
            if (
                isinstance(err_body, dict)
                and "offset exceeds maximum" in str(err_body.get("error", ""))
            ):
                return []
            resp.raise_for_status()

        data = resp.json()
        if isinstance(data, dict) and "offset exceeds maximum" in str(
            data.get("error", "")
        ):
            return []
        if not isinstance(data, list):
            raise RuntimeError(
                f"Unexpected non-list response: {type(data).__name__}: {data!r}"
            )
        return data

    raise RuntimeError(
        f"Gamma kept failing after {MAX_RETRIES} retries (params={params}): "
        f"last exception {last_exc!r}"
    )


# --------------------------------------------------------------------------- #
# Crawl drivers.                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class CrawlStats:
    pages_fetched: int = 0
    raw_records_seen: int = 0
    unique_market_ids: int = 0
    unique_event_ids: int = 0
    rate_limit_hits: int = 0


def _paginate_filter(
    url: str,
    base_params: dict[str, Any],
    session: requests.Session,
    *,
    label: str,
    stats: CrawlStats,
) -> Iterable[dict[str, Any]]:
    """Yield every record from a single filter combination."""

    offset = 0
    while offset <= GAMMA_OFFSET_CEILING:
        params = {
            "limit": DEFAULT_PAGE_SIZE,
            "offset": offset,
            **base_params,
        }
        page = _request_page(url, params, session)
        stats.pages_fetched += 1
        if not page:
            LOGGER.debug(
                "%s: empty page at offset=%d, stopping", label, offset
            )
            return
        stats.raw_records_seen += len(page)
        for rec in page:
            yield rec
        if len(page) < DEFAULT_PAGE_SIZE:
            LOGGER.debug(
                "%s: short page (%d < %d) at offset=%d, stopping",
                label,
                len(page),
                DEFAULT_PAGE_SIZE,
                offset,
            )
            return
        offset += DEFAULT_PAGE_SIZE


def _filter_combinations() -> list[tuple[str, dict[str, Any]]]:
    """Enumerate every (label, params) combination we'll crawl."""

    combos: list[tuple[str, dict[str, Any]]] = []
    for closed_flag in ("true", "false"):
        for start_min, start_max in DATE_WINDOWS:
            params: dict[str, Any] = {"closed": closed_flag}
            if start_min:
                params["start_date_min"] = start_min
            if start_max:
                params["start_date_max"] = start_max
            window = f"{start_min or '*'}_{start_max or '*'}"
            combos.append((f"closed={closed_flag}|win={window}", params))
    # archived=true rarely overlaps and is cheap.
    combos.append(("archived=true", {"archived": "true"}))
    return combos


def crawl_events(session: requests.Session) -> dict[str, dict[str, Any]]:
    """Return a ``event_id -> {category, tag_labels}`` lookup."""

    LOGGER.info("phase=events building category/tag lookup")
    lookup: dict[str, dict[str, Any]] = {}
    stats = CrawlStats()
    for label, params in _filter_combinations():
        for ev in _paginate_filter(
            GAMMA_EVENTS_URL, params, session, label=label, stats=stats
        ):
            eid = str(ev.get("id") or "")
            if not eid or eid in lookup:
                continue
            tags = ev.get("tags") or []
            tag_labels = [
                t.get("label")
                for t in tags
                if isinstance(t, dict) and t.get("label") and t.get("label") != "All"
            ]
            lookup[eid] = {
                "category": ev.get("category") or "",
                "tags": tag_labels,
            }
        LOGGER.info(
            "events phase: combo=%s cumulative_events=%d",
            label,
            len(lookup),
        )
    stats.unique_event_ids = len(lookup)
    LOGGER.info(
        "events phase done: %d unique events (%d pages, %d raw)",
        stats.unique_event_ids,
        stats.pages_fetched,
        stats.raw_records_seen,
    )
    return lookup


# --------------------------------------------------------------------------- #
# Normalization.                                                              #
# --------------------------------------------------------------------------- #


def _safe_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() == "true"
    return bool(v)


def _maybe_json(v: Any) -> Any:
    """Parse a JSON string back to list/dict, else return as-is."""
    if isinstance(v, (list, dict)):
        return v
    if isinstance(v, str) and v.startswith(("[", "{")):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


def _derive_state(market: dict[str, Any]) -> str:
    """Best-effort categorical state from boolean fields."""

    if _safe_bool(market.get("archived")):
        return "archived"
    if _safe_bool(market.get("closed")):
        statuses = market.get("umaResolutionStatuses") or []
        if any(
            isinstance(s, str) and "dispute" in s.lower() for s in statuses
        ):
            return "disputed"
        outcome_prices = _maybe_json(market.get("outcomePrices"))
        if isinstance(outcome_prices, list) and outcome_prices:
            return "resolved"
        return "closed"
    if _safe_bool(market.get("active")):
        return "active"
    return "inactive"


def _derive_outcome(market: dict[str, Any]) -> Optional[str]:
    """Categorical outcome for resolved markets (YES/NO/DISPUTED/None)."""

    outcomes = _maybe_json(market.get("outcomes"))
    prices = _maybe_json(market.get("outcomePrices"))
    if not isinstance(outcomes, list) or not isinstance(prices, list):
        return None
    if len(outcomes) != len(prices) or len(outcomes) < 2:
        return None
    # If any UMA status mentions dispute, that overrides price-based reading.
    statuses = market.get("umaResolutionStatuses") or []
    if any(isinstance(s, str) and "dispute" in s.lower() for s in statuses):
        return "DISPUTED"
    # Highest-price outcome is the resolved side; require ~1.0.
    try:
        floats = [float(p) for p in prices]
    except (TypeError, ValueError):
        return None
    top_idx = max(range(len(floats)), key=lambda i: floats[i])
    if floats[top_idx] < 0.999:
        # Not cleanly resolved; could be mid-trading.
        return None
    label = str(outcomes[top_idx]).strip().upper() or None
    return label


def _normalize_market(
    market: dict[str, Any],
    event_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Project a raw Gamma market into our wide row schema.

    All raw fields are preserved verbatim in ``all_raw_fields``.
    """

    market_id = str(market.get("id") or market.get("conditionId") or "")
    question = (market.get("question") or "").strip()

    # Event-derived enrichment (category, tags) — pick the first event
    # whose id we have in lookup.
    embedded_events = market.get("events") or []
    category: Optional[str] = None
    subcategory: Optional[str] = None
    tags: list[str] = []
    event_id: Optional[str] = None
    for ev in embedded_events:
        eid = str(ev.get("id") or "")
        if not eid:
            continue
        event_id = event_id or eid
        info = event_lookup.get(eid)
        if info:
            if not category and info.get("category"):
                category = info["category"]
            if not tags and info.get("tags"):
                tags = list(info["tags"])
            break

    outcome_prices = _maybe_json(market.get("outcomePrices"))
    clob_token_ids = _maybe_json(market.get("clobTokenIds"))
    yes_token_id = None
    no_token_id = None
    outcomes_raw = _maybe_json(market.get("outcomes"))
    if (
        isinstance(outcomes_raw, list)
        and isinstance(clob_token_ids, list)
        and len(outcomes_raw) == len(clob_token_ids)
    ):
        for label, token in zip(outcomes_raw, clob_token_ids):
            lab = str(label).strip().upper()
            if lab == "YES":
                yes_token_id = str(token)
            elif lab == "NO":
                no_token_id = str(token)

    state = _derive_state(market)
    resolved_at: Optional[str] = None
    if state in {"resolved", "closed", "disputed"}:
        resolved_at = market.get("updatedAt")

    uma_statuses = market.get("umaResolutionStatuses") or []
    uma_dispute = any(
        isinstance(s, str) and "dispute" in s.lower() for s in uma_statuses
    )

    fee_schedule = market.get("feeSchedule") or {}
    fee_tier = None
    if isinstance(fee_schedule, dict):
        fee_tier = _safe_float(
            fee_schedule.get("maker") or fee_schedule.get("taker")
        )

    return {
        "market_id": market_id,
        "question": question,
        "category": category,
        "subcategory": subcategory,
        "tags": tags,
        "event_id": event_id,
        "created_at": market.get("createdAt") or "",
        "updated_at": market.get("updatedAt") or "",
        "start_date": market.get("startDate") or "",
        "end_date": market.get("endDate") or None,
        "resolved_at": resolved_at,
        "state": state,
        "outcome": _derive_outcome(market),
        "outcome_prices": outcome_prices if isinstance(outcome_prices, list) else None,
        "outcome_token_ids": clob_token_ids if isinstance(clob_token_ids, list) else None,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "total_volume_usdc": _safe_float(
            market.get("volumeNum") or market.get("volume")
        ),
        "total_liquidity_usdc": _safe_float(
            market.get("liquidityNum") or market.get("liquidity")
        ),
        "volume_24hr": _safe_float(market.get("volume24hr")),
        "volume_1wk": _safe_float(market.get("volume1wk")),
        "volume_1mo": _safe_float(market.get("volume1mo")),
        "volume_1yr": _safe_float(market.get("volume1yr")),
        "best_bid": _safe_float(market.get("bestBid")),
        "best_ask": _safe_float(market.get("bestAsk")),
        "last_trade_price": _safe_float(market.get("lastTradePrice")),
        "spread": _safe_float(market.get("spread")),
        "trade_count": _safe_int(market.get("tradeCount")),
        "uma_dispute": uma_dispute,
        "uma_resolution_statuses": uma_statuses,
        "resolution_source": market.get("resolutionSource") or None,
        "creator_address": market.get("submitted_by") or None,
        "is_community_created": _safe_bool(market.get("cyom")),
        "fee_tier": fee_tier,
        "neg_risk": _safe_bool(market.get("negRisk")) if market.get("negRisk") is not None else None,
        "approved": _safe_bool(market.get("approved")) if market.get("approved") is not None else None,
        "active": _safe_bool(market.get("active")),
        "closed": _safe_bool(market.get("closed")),
        "archived": _safe_bool(market.get("archived")),
        "restricted": _safe_bool(market.get("restricted")),
        "slug": market.get("slug") or None,
        "description": market.get("description") or "",
        "condition_id": market.get("conditionId") or None,
        "question_id": market.get("questionID") or None,
        "all_raw_fields": market,
    }


# --------------------------------------------------------------------------- #
# Persistence.                                                                #
# --------------------------------------------------------------------------- #


def _to_jsonable(v: Any) -> Any:
    """Recursively coerce to JSON-safe primitives."""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {k: _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    return str(v)


def write_parquet(rows: list[dict[str, Any]], dest: Path) -> None:
    if not rows:
        LOGGER.warning("write_parquet: no rows to write to %s", dest)
        return
    df = pd.DataFrame(rows)
    # Parquet can't store dict in object column well — JSON-encode raw + lists.
    for col in ("all_raw_fields",):
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: json.dumps(_to_jsonable(v)) if v is not None else None
            )
    for col in ("tags", "outcome_prices", "outcome_token_ids", "uma_resolution_statuses"):
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: json.dumps(_to_jsonable(v)) if v is not None else None
            )
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dest, index=False)


def write_jsonl(rows: list[dict[str, Any]], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(_to_jsonable(r), ensure_ascii=False) + "\n")


def write_csv(rows: list[dict[str, Any]], dest: Path, *, limit: Optional[int] = None) -> None:
    if not rows:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Use a stable column order (drop the giant raw blob from the sample CSV).
    cols = [
        "market_id", "question", "category", "tags", "event_id",
        "created_at", "updated_at", "start_date", "end_date", "resolved_at",
        "state", "outcome", "outcome_prices",
        "total_volume_usdc", "total_liquidity_usdc",
        "volume_24hr", "volume_1wk", "volume_1mo", "volume_1yr",
        "best_bid", "best_ask", "last_trade_price", "spread",
        "uma_dispute", "resolution_source",
        "creator_address", "is_community_created",
        "fee_tier", "neg_risk", "active", "closed", "archived", "restricted",
        "slug", "condition_id", "question_id", "description",
    ]
    capped = rows[:limit] if limit else rows
    with dest.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in capped:
            cleaned = {}
            for c in cols:
                v = r.get(c)
                if isinstance(v, (list, dict)):
                    cleaned[c] = json.dumps(_to_jsonable(v), ensure_ascii=False)
                else:
                    cleaned[c] = v
            w.writerow(cleaned)


# --------------------------------------------------------------------------- #
# Top-level driver.                                                           #
# --------------------------------------------------------------------------- #


def discover_fields(session: requests.Session, dest: Path) -> None:
    """Hit /markets?limit=1, snapshot the field list for documentation."""

    LOGGER.info("phase=discovery probing /markets schema")
    page = _request_page(GAMMA_MARKETS_URL, {"limit": 1}, session)
    fields_info: dict[str, Any] = {
        "endpoint": GAMMA_MARKETS_URL,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "market_top_level_fields": sorted(page[0].keys()) if page else [],
    }
    ev_page = _request_page(GAMMA_EVENTS_URL, {"limit": 1}, session)
    fields_info["event_top_level_fields"] = (
        sorted(ev_page[0].keys()) if ev_page else []
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(fields_info, indent=2))
    LOGGER.info(
        "discovery wrote %d market fields, %d event fields",
        len(fields_info["market_top_level_fields"]),
        len(fields_info["event_top_level_fields"]),
    )


def crawl_markets(
    session: requests.Session,
    event_lookup: dict[str, dict[str, Any]],
    out_dir: Path,
) -> list[dict[str, Any]]:
    """Crawl all markets, dedup by market_id, persist incrementally."""

    LOGGER.info("phase=markets begin")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    stats = CrawlStats()
    incremental_path = out_dir / "polymarket_all_markets.parquet"
    last_persist = 0

    for label, params in _filter_combinations():
        before = len(rows)
        for market in _paginate_filter(
            GAMMA_MARKETS_URL, params, session, label=label, stats=stats
        ):
            mid = str(market.get("id") or market.get("conditionId") or "")
            if not mid or mid in seen_ids:
                continue
            seen_ids.add(mid)
            rows.append(_normalize_market(market, event_lookup))
            if len(rows) % PROGRESS_EVERY == 0:
                LOGGER.info(
                    "progress: %d unique markets (combo=%s)", len(rows), label
                )
            if len(rows) >= HARD_CAP:
                LOGGER.warning(
                    "HARD_CAP %d reached, stopping early", HARD_CAP
                )
                break
            if len(rows) - last_persist >= PERSIST_EVERY:
                LOGGER.info(
                    "incremental persist: writing %d rows -> %s",
                    len(rows),
                    incremental_path,
                )
                write_parquet(rows, incremental_path)
                last_persist = len(rows)
        LOGGER.info(
            "combo done: %s | added %d (total=%d)",
            label,
            len(rows) - before,
            len(rows),
        )
        if len(rows) >= HARD_CAP:
            break

    stats.unique_market_ids = len(rows)
    LOGGER.info(
        "markets phase done: %d unique markets (%d pages, %d raw)",
        stats.unique_market_ids,
        stats.pages_fetched,
        stats.raw_records_seen,
    )
    return rows


# --------------------------------------------------------------------------- #
# Stats reporting.                                                            #
# --------------------------------------------------------------------------- #


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(round((len(sorted_vals) - 1) * p))
    return sorted_vals[idx]


def compute_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    state_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    year_counts: Counter[str] = Counter()
    uma_by_cat: dict[str, list[int]] = defaultdict(list)
    volumes: list[float] = []
    top_market = {"market_id": None, "question": None, "volume": 0.0}

    for r in rows:
        state_counts[r.get("state") or "unknown"] += 1
        cat = r.get("category") or "(uncategorized)"
        category_counts[cat] += 1
        created = (r.get("created_at") or "")[:4]
        if created.isdigit():
            year_counts[created] += 1
        uma_by_cat[cat].append(1 if r.get("uma_dispute") else 0)
        vol = r.get("total_volume_usdc")
        if isinstance(vol, (int, float)) and vol > 0:
            volumes.append(float(vol))
            if vol > top_market["volume"]:
                top_market = {
                    "market_id": r.get("market_id"),
                    "question": r.get("question"),
                    "volume": float(vol),
                }

    volumes.sort()
    volume_stats = {
        "n": len(volumes),
        "total_usdc": sum(volumes),
        "p50": _percentile(volumes, 0.50),
        "p90": _percentile(volumes, 0.90),
        "p99": _percentile(volumes, 0.99),
        "max": volumes[-1] if volumes else 0.0,
        "histogram_buckets": {
            "0-100": sum(1 for v in volumes if v < 100),
            "100-1k": sum(1 for v in volumes if 100 <= v < 1_000),
            "1k-10k": sum(1 for v in volumes if 1_000 <= v < 10_000),
            "10k-100k": sum(1 for v in volumes if 10_000 <= v < 100_000),
            "100k-1M": sum(1 for v in volumes if 100_000 <= v < 1_000_000),
            "1M+": sum(1 for v in volumes if v >= 1_000_000),
        },
    }

    uma_rate = {
        cat: {"n": len(v), "dispute_rate": (sum(v) / len(v)) if v else 0.0}
        for cat, v in uma_by_cat.items()
    }
    uma_overall = (
        sum(1 for r in rows if r.get("uma_dispute")) / len(rows)
        if rows
        else 0.0
    )

    return {
        "total_markets": len(rows),
        "state_distribution": dict(state_counts),
        "category_distribution": dict(category_counts.most_common()),
        "year_distribution": dict(sorted(year_counts.items())),
        "volume_distribution": volume_stats,
        "uma_dispute_rate_overall": uma_overall,
        "uma_dispute_rate_by_category": uma_rate,
        "top_market_by_volume": top_market,
    }


def write_stats_outputs(stats: dict[str, Any], out_dir: Path) -> None:
    (out_dir / "distribution_by_state.json").write_text(
        json.dumps(stats["state_distribution"], indent=2)
    )
    (out_dir / "distribution_by_category.json").write_text(
        json.dumps(stats["category_distribution"], indent=2)
    )
    (out_dir / "distribution_by_year.json").write_text(
        json.dumps(stats["year_distribution"], indent=2)
    )
    (out_dir / "volume_distribution.json").write_text(
        json.dumps(stats["volume_distribution"], indent=2)
    )

    md_lines: list[str] = []
    md_lines.append("# Polymarket Full Corpus Summary\n")
    md_lines.append(
        f"_Generated {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}_\n"
    )
    md_lines.append(f"\n## Totals\n- **Total markets**: {stats['total_markets']:,}\n")

    md_lines.append("\n## State distribution\n")
    for k, v in sorted(
        stats["state_distribution"].items(), key=lambda kv: -kv[1]
    ):
        md_lines.append(f"- `{k}`: {v:,}\n")

    md_lines.append("\n## Top 20 categories\n")
    for i, (k, v) in enumerate(
        list(stats["category_distribution"].items())[:20]
    ):
        md_lines.append(f"{i+1}. `{k}`: {v:,}\n")

    md_lines.append("\n## Year distribution (by createdAt)\n")
    for y, n in stats["year_distribution"].items():
        md_lines.append(f"- {y}: {n:,}\n")

    vd = stats["volume_distribution"]
    md_lines.append("\n## Volume (USDC)\n")
    md_lines.append(f"- markets with volume > 0: {vd['n']:,}\n")
    md_lines.append(f"- total volume: ${vd['total_usdc']:,.0f}\n")
    md_lines.append(f"- P50: ${vd['p50']:,.2f}\n")
    md_lines.append(f"- P90: ${vd['p90']:,.2f}\n")
    md_lines.append(f"- P99: ${vd['p99']:,.2f}\n")
    md_lines.append(f"- Max: ${vd['max']:,.2f}\n")
    md_lines.append("\n### Volume histogram\n")
    for bucket, n in vd["histogram_buckets"].items():
        md_lines.append(f"- {bucket}: {n:,}\n")

    md_lines.append("\n## Top market by volume\n")
    tm = stats["top_market_by_volume"]
    md_lines.append(
        f"- **{tm['question']}** — ${tm['volume']:,.0f} (`market_id={tm['market_id']}`)\n"
    )

    md_lines.append(
        f"\n## UMA dispute rate\n- Overall: {stats['uma_dispute_rate_overall']:.4%}\n"
    )
    md_lines.append("\n### Top categories by dispute rate (n>=20)\n")
    high_rate = sorted(
        (
            (cat, info)
            for cat, info in stats["uma_dispute_rate_by_category"].items()
            if info["n"] >= 20
        ),
        key=lambda kv: -kv[1]["dispute_rate"],
    )[:20]
    for cat, info in high_rate:
        md_lines.append(
            f"- `{cat}`: {info['dispute_rate']:.2%} (n={info['n']})\n"
        )

    (out_dir / "full_summary.md").write_text("".join(md_lines))


# --------------------------------------------------------------------------- #
# CLI.                                                                        #
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", default="corpus/full", help="Output directory"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING"),
    )
    parser.add_argument(
        "--skip-events",
        action="store_true",
        help="Skip the /events crawl (category/tags will be missing)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.setdefault("User-Agent", "polyglot-alpha-full-scraper/0.1")

    discover_fields(session, out_dir / "api_fields.json")

    event_lookup: dict[str, dict[str, Any]] = {}
    if not args.skip_events:
        event_lookup = crawl_events(session)
        (out_dir / "_event_lookup_count.json").write_text(
            json.dumps({"events_indexed": len(event_lookup)})
        )

    try:
        rows = crawl_markets(session, event_lookup, out_dir)
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted — flushing what we have so far")
        rows = []  # incremental file already on disk
        return 130

    LOGGER.info("writing final parquet/jsonl/csv outputs")
    write_parquet(rows, out_dir / "polymarket_all_markets.parquet")
    write_jsonl(rows, out_dir / "polymarket_all_markets.jsonl")
    write_csv(rows, out_dir / "polymarket_all_markets.csv")
    write_csv(rows, out_dir / "polymarket_all_markets_sample.csv", limit=1000)

    LOGGER.info("computing statistics")
    stats = compute_stats(rows)
    write_stats_outputs(stats, out_dir)
    LOGGER.info(
        "done — %d markets persisted to %s", len(rows), out_dir.resolve()
    )

    # Sanity check counts across formats.
    pq = pd.read_parquet(out_dir / "polymarket_all_markets.parquet")
    with (out_dir / "polymarket_all_markets.jsonl").open() as fh:
        jl_count = sum(1 for _ in fh)
    # CSV cells contain embedded newlines from descriptions; rely on csv reader.
    with (out_dir / "polymarket_all_markets.csv").open() as fh:
        reader = csv.reader(fh)
        next(reader, None)  # header
        csv_count = sum(1 for _ in reader)
    LOGGER.info(
        "validation: parquet=%d jsonl=%d csv=%d",
        len(pq),
        jl_count,
        csv_count,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
