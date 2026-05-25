"""Convenience loader: outputs/ground_truth/*.json -> ReferenceTranslation rows.

This is a thin wrapper around ``db_ingestion.ingest_reference_translations``
that exposes a clean single-purpose API for callers (T4 judges, demo
scripts, eval harness).

CLI:
    python -m polyglot_alpha.corpus.reference_loader \\
        --path outputs/ground_truth/
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Optional

from polyglot_alpha.corpus.db_ingestion import (
    DEFAULT_REFERENCES_DIR,
    IngestStats,
    ingest_reference_translations,
)
from polyglot_alpha.persistence import db as persistence_db
from polyglot_alpha.persistence import init_db
from polyglot_alpha.persistence.models import ReferenceTranslation
from sqlmodel import Session


def _read_session() -> Session:
    """Internal helper: read-only session that keeps attributes alive."""

    return Session(persistence_db.engine, expire_on_commit=False)

LOGGER = logging.getLogger(__name__)


async def load_references(
    path: Path = DEFAULT_REFERENCES_DIR,
    *,
    ensure_schema: bool = True,
) -> IngestStats:
    """Ingest ground-truth JSON files into ``reference_translations``."""

    if ensure_schema:
        init_db()
    return await ingest_reference_translations(path)


def get_reference(sample_id: int) -> Optional[ReferenceTranslation]:
    """Synchronous DB fetch for a single reference translation."""

    with _read_session() as session:
        row = session.get(ReferenceTranslation, sample_id)
        if row is not None:
            session.expunge(row)
        return row


def list_references(limit: int = 100) -> list[ReferenceTranslation]:
    """Synchronous DB fetch for all reference translations."""

    from sqlalchemy import select

    with _read_session() as session:
        stmt = (
            select(ReferenceTranslation)
            .order_by(ReferenceTranslation.sample_id.asc())
            .limit(limit)
        )
        rows = list(session.execute(stmt).scalars())
        for r in rows:
            session.expunge(r)
        return rows


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load human-verified reference translations into the DB."
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_REFERENCES_DIR,
        help="Directory of JSON files or a single JSONL file.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    stats = asyncio.run(load_references(args.path))
    LOGGER.info(
        "References loaded: %d inserted, %d updated, %d skipped",
        stats.inserted,
        stats.updated,
        stats.skipped,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
