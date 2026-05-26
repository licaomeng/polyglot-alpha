#!/usr/bin/env python3
"""One-shot: seed ``corpus_markets`` from Polymarket Gamma or local JSON.

This is the minimum-viable ingestion path required by W13-A to give the
D8 duplicate-detection judge and the few-shot judges a non-empty
``corpus_markets`` table to query against.

Default behaviour:
  1. Try the public Polymarket Gamma ``/markets`` endpoint (no auth, no
     API key). Pull up to ``--max-rows`` recent markets and upsert via
     ``polyglot_alpha.corpus.db_ingestion._upsert_corpus_market``.
  2. If the Gamma call fails (network unreachable, validation error,
     timeout, non-200) — fall back to the hand-curated
     ``polyglot_alpha/corpus/seed_markets.json``. This guarantees the
     table has at least ~15 rows so downstream code path tests don't
     fail just because the network is offline.

Idempotent: re-running upserts on ``market_id`` so existing rows are
updated, not duplicated. Safe to schedule.

Usage::

    .venv/bin/python scripts/ingest_corpus_markets.py
    .venv/bin/python scripts/ingest_corpus_markets.py --max-rows 200
    .venv/bin/python scripts/ingest_corpus_markets.py --seed-only
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import requests

from polyglot_alpha.corpus.db_ingestion import (
    IngestStats,
    _row_to_corpus_market_kwargs,
    _upsert_corpus_market,
)
from polyglot_alpha.persistence import init_db, session_scope


LOGGER = logging.getLogger("ingest_corpus_markets")

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_ROWS = 500
DEFAULT_TIMEOUT_S = 15
SEED_PATH = (
    Path(__file__).resolve().parents[1]
    / "polyglot_alpha"
    / "corpus"
    / "seed_markets.json"
)


def _coerce_gamma_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map a Gamma ``/markets`` row to the ``_row_to_corpus_market_kwargs`` shape.

    Gamma's field names diverge slightly from the parquet schema; here we
    normalize the minimum subset we need (id, question text, tags,
    state-relevant flags) without re-implementing the full crawler in
    ``polyglot_alpha.corpus.full_scraper``.
    """

    market_id = raw.get("id") or raw.get("conditionId") or raw.get("marketId")
    question = raw.get("question") or raw.get("title") or ""
    if not market_id or not str(question).strip():
        return None
    return {
        "market_id": market_id,
        "question": question,
        "category": raw.get("category"),
        "subcategory": raw.get("subcategory"),
        "tags": raw.get("tags") or None,
        "created_at": raw.get("createdAt"),
        "end_date": raw.get("endDate") or raw.get("resolutionDate"),
        "resolved_at": raw.get("resolvedAt"),
        "closed": raw.get("closed", False),
        "outcome": raw.get("outcome") or raw.get("winningOutcome"),
        "outcome_prices": raw.get("outcomePrices"),
        "total_volume_usdc": raw.get("volumeNum") or raw.get("volume"),
        "uma_dispute": raw.get("umaDispute", False),
        "resolution_source": raw.get("resolutionSource"),
        "is_community_created": raw.get("isCommunityCreated", False),
    }


def _fetch_gamma_page(
    session: requests.Session, offset: int, page_size: int
) -> list[dict[str, Any]]:
    params = {"limit": page_size, "offset": offset, "ascending": "false"}
    resp = session.get(GAMMA_MARKETS_URL, params=params, timeout=DEFAULT_TIMEOUT_S)
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, list):
        raise RuntimeError(f"Unexpected Gamma response shape: {type(body).__name__}")
    return body


def _ingest_from_gamma(max_rows: int, page_size: int) -> IngestStats:
    stats = IngestStats()
    session = requests.Session()
    offset = 0
    fetched = 0
    while fetched < max_rows:
        page = _fetch_gamma_page(session, offset=offset, page_size=page_size)
        if not page:
            break
        with session_scope() as dbsess:
            for raw in page:
                normalized = _coerce_gamma_row(raw)
                if normalized is None:
                    stats.skipped += 1
                    continue
                kwargs = _row_to_corpus_market_kwargs(normalized)
                if kwargs is None:
                    stats.skipped += 1
                    continue
                if _upsert_corpus_market(dbsess, kwargs):
                    stats.inserted += 1
                else:
                    stats.updated += 1
        fetched += len(page)
        offset += len(page)
        if len(page) < page_size:
            break
    LOGGER.info(
        "gamma: inserted=%d updated=%d skipped=%d (fetched=%d)",
        stats.inserted,
        stats.updated,
        stats.skipped,
        fetched,
    )
    return stats


def _ingest_from_seed() -> IngestStats:
    stats = IngestStats()
    if not SEED_PATH.exists():
        LOGGER.error("seed file missing: %s", SEED_PATH)
        return stats
    payload = json.loads(SEED_PATH.read_text())
    rows = payload.get("markets") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        LOGGER.error("unexpected seed shape: %r", type(payload).__name__)
        return stats
    with session_scope() as dbsess:
        for raw in rows:
            kwargs = _row_to_corpus_market_kwargs(raw)
            if kwargs is None:
                stats.skipped += 1
                continue
            if _upsert_corpus_market(dbsess, kwargs):
                stats.inserted += 1
            else:
                stats.updated += 1
    LOGGER.info(
        "seed: inserted=%d updated=%d skipped=%d (%d rows)",
        stats.inserted,
        stats.updated,
        stats.skipped,
        len(rows),
    )
    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-rows",
        type=int,
        default=DEFAULT_MAX_ROWS,
        help="Maximum Gamma rows to fetch (default: %(default)s).",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="Gamma page size (default: %(default)s).",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Skip Gamma; ingest from seed_markets.json directly.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    args = _build_parser().parse_args(argv)

    init_db()

    if args.seed_only:
        LOGGER.info("--seed-only: ingesting from %s", SEED_PATH)
        _ingest_from_seed()
        return 0

    try:
        stats = _ingest_from_gamma(max_rows=args.max_rows, page_size=args.page_size)
        if stats.inserted + stats.updated == 0:
            LOGGER.warning(
                "Gamma returned zero usable rows; falling back to seed"
            )
            _ingest_from_seed()
        return 0
    except Exception as exc:  # noqa: BLE001 — fall back to seed on any failure
        LOGGER.warning(
            "Gamma fetch failed (%s); falling back to %s", exc, SEED_PATH
        )
        _ingest_from_seed()
        return 0


if __name__ == "__main__":
    sys.exit(main())
