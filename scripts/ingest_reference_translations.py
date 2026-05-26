#!/usr/bin/env python3
"""One-shot: ingest ``outputs/ground_truth/*.json`` -> ``reference_translations``.

This is a thin wrapper around
``polyglot_alpha.corpus.reference_loader.load_references`` so the
ingestion path can be invoked from CI / ops without remembering the
module name.

Idempotent (the underlying ``_sync_ingest_reference_translations``
upserts on ``sample_id``).

Usage::

    .venv/bin/python scripts/ingest_reference_translations.py
    .venv/bin/python scripts/ingest_reference_translations.py --path /path/to/dir
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from polyglot_alpha.corpus.db_ingestion import DEFAULT_REFERENCES_DIR
from polyglot_alpha.corpus.reference_loader import load_references


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_REFERENCES_DIR,
        help=(
            "Directory of *_ground_truth.json files or a .jsonl file. "
            "Default: %(default)s"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    log = logging.getLogger("ingest_reference_translations")
    args = _build_parser().parse_args(argv)

    if not args.path.exists():
        log.error(
            "reference path not found: %s — create the directory or pass --path",
            args.path,
        )
        return 1

    stats = asyncio.run(load_references(args.path))
    log.info(
        "done: inserted=%d updated=%d skipped=%d (total=%d)",
        stats.inserted,
        stats.updated,
        stats.skipped,
        stats.total,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
