"""Scrape Polymarket *resolved* markets into a ground-truth dataset.

This is the sibling of ``scraper.py`` (which collects open + closed
markets for style / few-shot purposes). This module's purpose is
different: we want a labelled corpus for **backtest and reputation
validation** of the 4-agent system, so we keep only markets that have
actually resolved, and we record the resolution outcome, dispute trace,
and final tradable prices.

Data source: ``https://gamma-api.polymarket.com/markets`` with
``closed=true``. The deprecated list endpoint is used because the newer
``/markets/keyset`` endpoint hard-caps the page size at 100 *and*
returns the same payload shape, while keyset cursors expire mid-crawl;
``/markets`` allows ``offset`` up to ~10000 (we stay well under) which
is more than enough for 5K rows.

UMA dispute signal: the ``umaResolutionStatuses`` field is a JSON-encoded
list of status transitions, e.g. ``["proposed", "disputed", "proposed",
"resolved"]``. Any presence of the literal ``"disputed"`` token means
the market went through at least one UMA challenge cycle — this is the
**D5 calibration signal** referenced in the project plan.

Output schema:

    market_id          str    Polymarket numeric id (primary key)
    question           str    English question text
    category           str    free-form category (e.g. "Sports")
    created_at         str    ISO-8601 creation timestamp
    end_date           str    ISO-8601 scheduled end date
    resolved_at        str    ISO-8601 actual close time
    outcome            str    YES / NO / DISPUTED / REFUNDED / UNKNOWN
    outcome_prices     list   final tradable prices at close
    total_volume_usdc  float  lifetime volume in USDC
    uma_dispute        bool   True iff UMA dispute history is present
    resolution_source  str    URL or source name
    winning_outcome    str    label of the winning outcome (or None)
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx
import pandas as pd

LOGGER = logging.getLogger(__name__)

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
DEFAULT_PAGE_SIZE = 100  # the deprecated endpoint hard-caps responses at 100
DEFAULT_TARGET = 5000
DEFAULT_TIMEOUT_S = 30
MAX_BACKOFF_S = 60
MAX_OFFSET = 9900  # Gamma rejects offset > ~10000 on /markets
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Tokens we look for in umaResolutionStatuses to classify outcomes.
_UMA_DISPUTED_TOKEN = "disputed"
_UMA_RESOLVED_TOKEN = "resolved"

# Winning-price threshold: outcomes have *near-1* / *near-0* prices on
# resolved binary markets. Use a loose threshold so deep-decimal floats
# like "0.9999997..." count as a clean YES.
_WIN_PRICE_THRESHOLD = 0.95

# Category fallback: the newer Gamma `/markets` payload often has
# ``category=None``; the event title still encodes a useful coarse tag.
# We do simple keyword matching against the question+event_title; the
# first hit wins, otherwise we return "Other". This is good enough for
# the by-category statistics in outcome_distribution.json.
_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Crypto", ("btc", "bitcoin", "eth", "ethereum", "fdv", "token", "crypto",
                "solana", "doge", "memecoin", "stablecoin", "usdt", "usdc",
                "binance", "coinbase", "airdrop", "depin", "defi")),
    ("Sports", ("nba", "nhl", "nfl", "mlb", "ufc", "league", "champion",
                "vs.", "vs ", "win the", "f1 ", "tennis", "soccer", "rugby",
                "cricket", "epl", "uefa", "stanley cup", "world cup",
                "playoff", "bracket", "tournament", "race", "match",
                "scheduled for")),
    ("Politics", ("president", "election", "trump", "biden", "harris",
                  "putin", "zelensky", "congress", "senate", "house",
                  "vote", "primary", "governor", "minister", "parliament",
                  "cabinet", "nominate", "impeach", "campaign")),
    ("Economics", ("fed", "rate cut", "rate hike", "inflation", "cpi",
                   "gdp", "recession", "jobless", "unemployment", "spx",
                   "s&p", "nasdaq", "dow", "tariff", "oil", "wti", "gold",
                   "silver", "ipo")),
    ("Geopolitics", ("ukraine", "russia", "israel", "iran", "gaza",
                     "north korea", "china", "taiwan", "strike", "war",
                     "ceasefire", "deal", "sanction")),
    ("Tech", ("openai", "anthropic", "gpt", "claude", "google", "apple",
              "microsoft", "tesla", "nvidia", "ai ", " ai", "model")),
    ("Pop-Culture", ("oscar", "grammy", "emmy", "movie", "film", "song",
                     "album", "tv show", "music")),
    ("Weather", ("hurricane", "storm", "snowfall", "temperature", "rain",
                 "cyclone", "typhoon")),
]


def _derive_category(*, raw: str, question: str, event_title: str) -> str:
    """Return a non-empty category label, falling back to keyword match."""

    if raw:
        return raw.strip()
    haystack = f"{question} {event_title}".lower()
    for label, kws in _CATEGORY_KEYWORDS:
        for kw in kws:
            if kw in haystack:
                return label
    return "Other"


@dataclass(frozen=True)
class ResolvedRow:
    """One labelled row in the resolved-markets corpus."""

    market_id: str
    question: str
    category: str
    created_at: str
    end_date: str
    resolved_at: str
    outcome: str
    outcome_prices: list
    total_volume_usdc: float
    uma_dispute: bool
    resolution_source: str
    winning_outcome: Optional[str]


# --------------------------------------------------------------------------- #
# HTTP layer.                                                                 #
# --------------------------------------------------------------------------- #


def _request_markets_page(
    *,
    client: httpx.Client,
    limit: int,
    offset: int,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_retries: int = 5,
) -> list[dict[str, Any]]:
    """Fetch one page of closed markets, retrying transient failures."""

    params = {
        "closed": "true",
        "limit": limit,
        "offset": offset,
        # Most-recently-ended first — these have the freshest UMA traces
        # and are the most useful for backtest.
        "order": "endDate",
        "ascending": "false",
    }

    backoff_s = 1.0
    for attempt in range(max_retries):
        try:
            resp = client.get(GAMMA_MARKETS_URL, params=params, timeout=timeout_s)
        except httpx.HTTPError as e:
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
        if isinstance(data, dict) and data.get("type") == "validation error":
            # E.g. offset exceeds maximum allowed.
            raise RuntimeError(
                f"Gamma validation error at offset {offset}: {data.get('error')}"
            )
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


def _parse_json_list(raw: Any) -> list:
    """Best-effort parse of a field that may be a JSON-encoded list."""

    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _float_list(values: Iterable[Any]) -> list[float]:
    out: list[float] = []
    for v in values:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _detect_uma_dispute(raw_statuses: Any) -> bool:
    """True iff the UMA status transitions contain a 'disputed' entry."""

    statuses = _parse_json_list(raw_statuses)
    return any(
        isinstance(s, str) and _UMA_DISPUTED_TOKEN in s.lower() for s in statuses
    )


def _classify_outcome(
    *,
    outcomes: list,
    prices: list[float],
    uma_statuses: list,
    closed: bool,
) -> tuple[str, Optional[str]]:
    """Return ``(outcome_label, winning_outcome)``.

    ``outcome_label`` is one of YES / NO / DISPUTED / REFUNDED / UNKNOWN.
    YES/NO are reserved for binary markets where outcomes are literally
    ``Yes``/``No``; for other binary markets (e.g. team-vs-team Sports
    markets) we return the literal winning label as ``outcome`` too so
    downstream consumers don't lose information.
    """

    if not closed:
        return ("UNKNOWN", None)

    # Refunded / inconclusive: all prices zero or sum < threshold.
    if not prices or sum(prices) < 0.5:
        # If UMA disputed and never resolved, mark DISPUTED, else REFUNDED.
        if any(
            isinstance(s, str) and _UMA_DISPUTED_TOKEN in s.lower()
            for s in uma_statuses
        ) and not any(
            isinstance(s, str) and _UMA_RESOLVED_TOKEN in s.lower()
            for s in uma_statuses
        ):
            return ("DISPUTED", None)
        return ("REFUNDED", None)

    # Find winning outcome by max price.
    if len(prices) != len(outcomes) or not outcomes:
        return ("UNKNOWN", None)
    winner_idx = max(range(len(prices)), key=lambda i: prices[i])
    if prices[winner_idx] < _WIN_PRICE_THRESHOLD:
        # Ambiguous close — treat as inconclusive.
        return ("DISPUTED", None)

    winner_label = str(outcomes[winner_idx])
    norm = winner_label.strip().lower()
    if norm == "yes":
        return ("YES", winner_label)
    if norm == "no":
        return ("NO", winner_label)
    # Non-binary YES/NO market (e.g. "Hurricanes" vs "Bruins"): expose
    # the literal label so backtest can still pick a winner.
    return (winner_label, winner_label)


def market_to_row(market: dict[str, Any]) -> Optional[ResolvedRow]:
    """Convert a raw Gamma market dict to a ``ResolvedRow``.

    Returns ``None`` if the market is missing required fields.
    """

    market_id = str(market.get("id") or "")
    question = (market.get("question") or "").strip()
    if not market_id or not question:
        return None

    closed = bool(market.get("closed", False))
    if not closed:
        # Defensive: closed=true was requested but verify anyway.
        return None

    outcomes = _parse_json_list(market.get("outcomes"))
    prices_raw = _parse_json_list(market.get("outcomePrices"))
    prices = _float_list(prices_raw)

    if len(outcomes) != 2:
        # Restrict to binary markets — same constraint as the open-corpus
        # scraper. Non-binary markets don't fit the YES/NO framing.
        return None

    uma_statuses = _parse_json_list(market.get("umaResolutionStatuses"))
    uma_dispute = any(
        isinstance(s, str) and _UMA_DISPUTED_TOKEN in s.lower() for s in uma_statuses
    )

    outcome, winning = _classify_outcome(
        outcomes=outcomes,
        prices=prices,
        uma_statuses=uma_statuses,
        closed=closed,
    )

    try:
        volume = float(market.get("volumeNum") or market.get("volume") or 0)
    except (TypeError, ValueError):
        volume = 0.0

    # Derive category: prefer market.category, then event.category, then
    # keyword-match against the question + event title.
    events = market.get("events") or []
    event_title = ""
    raw_category = (market.get("category") or "").strip()
    if events:
        first_event = events[0]
        if isinstance(first_event, dict):
            event_title = first_event.get("title") or ""
            if not raw_category:
                raw_category = (first_event.get("category") or "").strip()
    category = _derive_category(
        raw=raw_category, question=question, event_title=event_title
    )

    return ResolvedRow(
        market_id=market_id,
        question=question,
        category=category,
        created_at=market.get("createdAt") or "",
        end_date=market.get("endDate") or market.get("endDateIso") or "",
        resolved_at=str(market.get("closedTime") or ""),
        outcome=outcome,
        outcome_prices=prices,
        total_volume_usdc=volume,
        uma_dispute=uma_dispute,
        resolution_source=(market.get("resolutionSource") or "").strip(),
        winning_outcome=winning,
    )


# --------------------------------------------------------------------------- #
# Crawl driver.                                                               #
# --------------------------------------------------------------------------- #


def scrape_resolved_markets(
    *,
    target_rows: int = DEFAULT_TARGET,
    page_size: int = DEFAULT_PAGE_SIZE,
    start_offset: int = 0,
    client: Optional[httpx.Client] = None,
) -> list[ResolvedRow]:
    """Crawl Gamma for closed/resolved binary markets."""

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": "polyglot-alpha-resolved/0.1"},
        )

    seen_ids: set[str] = set()
    rows: list[ResolvedRow] = []

    try:
        offset = start_offset
        empty_pages = 0
        while len(rows) < target_rows and offset <= MAX_OFFSET:
            try:
                page = _request_markets_page(
                    client=client, limit=page_size, offset=offset
                )
            except RuntimeError as e:
                LOGGER.warning("stopping crawl: %s", e)
                break

            if not page:
                empty_pages += 1
                if empty_pages >= 2:
                    LOGGER.info("two empty pages in a row, stopping")
                    break
                offset += page_size
                continue
            empty_pages = 0

            new_in_page = 0
            for market in page:
                row = market_to_row(market)
                if row is None:
                    continue
                if row.market_id in seen_ids:
                    continue
                seen_ids.add(row.market_id)
                rows.append(row)
                new_in_page += 1
                if len(rows) >= target_rows:
                    break

            LOGGER.info(
                "offset=%d page_total=%d new=%d total=%d",
                offset,
                len(page),
                new_in_page,
                len(rows),
            )
            offset += page_size
    finally:
        if owns_client:
            client.close()

    return rows


# --------------------------------------------------------------------------- #
# Persistence.                                                                #
# --------------------------------------------------------------------------- #


def rows_to_dataframe(rows: Iterable[ResolvedRow]) -> pd.DataFrame:
    return pd.DataFrame([asdict(r) for r in rows])


def save_outputs(
    rows: list[ResolvedRow],
    *,
    parquet_path: Path,
    csv_path: Path,
    jsonl_path: Path,
    csv_sample_size: int = 500,
) -> None:
    """Write parquet (full), CSV (sample), and JSONL (full streaming)."""

    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    df = rows_to_dataframe(rows)
    df.to_parquet(parquet_path, index=False)

    # CSV is a *sample*: the top-N most recent (= first N rows since we
    # crawl with order=endDate desc). This is for human eyeballing only.
    sample = df.head(csv_sample_size).copy()
    # Serialize list column for CSV friendliness.
    sample["outcome_prices"] = sample["outcome_prices"].apply(json.dumps)
    sample.to_csv(csv_path, index=False)

    # JSONL: stream all rows for downstream consumers.
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# CLI.                                                                        #
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-parquet", default="corpus/polymarket_resolved.parquet"
    )
    parser.add_argument("--out-csv", default="corpus/polymarket_resolved.csv")
    parser.add_argument("--out-jsonl", default="corpus/polymarket_resolved.jsonl")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument("--csv-sample", type=int, default=500)
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
        rows = scrape_resolved_markets(
            target_rows=args.target,
            page_size=args.page_size,
            start_offset=args.start_offset,
        )
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted; saving partial corpus")
        rows = []

    save_outputs(
        rows,
        parquet_path=Path(args.out_parquet),
        csv_path=Path(args.out_csv),
        jsonl_path=Path(args.out_jsonl),
        csv_sample_size=args.csv_sample,
    )
    LOGGER.info(
        "wrote %d rows -> %s / %s / %s",
        len(rows),
        args.out_parquet,
        args.out_csv,
        args.out_jsonl,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
