#!/usr/bin/env python3
"""One-shot: backfill ``polymarket_submissions.payload`` for legacy rows.

Background
----------
The ``payload`` column was added to ``polymarket_submissions`` in W2-1
(see ``polyglot_alpha.persistence.db._migrate_polymarket_submissions``).
Rows written before that migration were never given a payload — they sit
with ``payload IS NULL`` and break consumers that assume the column is
always populated (UI, audit replays, contract export).

This script synthesizes a minimal placeholder payload for every NULL row
so the column becomes uniformly non-null. The synthesized payload is
small + self-describing so future readers can tell it is reconstructed,
not original:

    {
      "synthesized": true,
      "reason": "Pre-W2-1 — payload retroactively reconstructed",
      "market_id": "<market_id from row>",
      "market_url": "<market_url from row>",
      "question": "<event.title joined via FK; null if event missing>",
      "submitted_at": "<ISO timestamp from row>",
      "status": "<status from row>"
    }

Idempotent: only NULL rows are touched.

Usage::

    .venv/bin/python scripts/backfill_polymarket_payload.py
    .venv/bin/python scripts/backfill_polymarket_payload.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlmodel import select

from polyglot_alpha.persistence import init_db, session_scope
from polyglot_alpha.persistence.models import Event, PolymarketSubmission


SYNTHESIZED_REASON = "Pre-W2-1 — payload retroactively reconstructed"


def _build_payload(sub: PolymarketSubmission, event_title: str | None) -> dict:
    return {
        "synthesized": True,
        "reason": SYNTHESIZED_REASON,
        "market_id": sub.market_id,
        "market_url": sub.market_url,
        "question": event_title,
        "submitted_at": (
            sub.submitted_at.isoformat() if sub.submitted_at is not None else None
        ),
        "status": sub.status,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the number of rows that would be backfilled, but don't write.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    log = logging.getLogger("backfill_polymarket_payload")
    args = _build_parser().parse_args(argv)

    init_db()

    with session_scope() as session:
        stmt = select(PolymarketSubmission).where(
            PolymarketSubmission.payload.is_(None)  # type: ignore[union-attr]
        )
        rows = session.exec(stmt).all()
        if not rows:
            log.info("no NULL-payload rows; nothing to do")
            return 0
        log.info("found %d NULL-payload row(s)", len(rows))
        if args.dry_run:
            return 0

        # Pre-fetch event titles by event_id to avoid N+1 selects on a
        # potentially large backfill.
        event_ids = {sub.event_id for sub in rows if sub.event_id is not None}
        event_titles: dict[int, str] = {}
        if event_ids:
            ev_stmt = select(Event.id, Event.title).where(Event.id.in_(event_ids))  # type: ignore[attr-defined]
            for ev_id, title in session.exec(ev_stmt).all():
                event_titles[ev_id] = title

        updated = 0
        for sub in rows:
            title = event_titles.get(sub.event_id) if sub.event_id is not None else None
            sub.payload = _build_payload(sub, title)
            session.add(sub)
            updated += 1
        log.info("backfilled %d row(s)", updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
