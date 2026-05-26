#!/usr/bin/env python3
"""One-shot: ingest ``EXTENDED_EXEMPLARS`` into ``few_shot_exemplars``.

This script bulk-loads the ~71 hand-curated D1/D3/D4/D5/D6/D7/D8 few-shot
exemplars defined in ``polyglot_alpha.corpus.few_shots_extended`` into the
local ``few_shot_exemplars`` table. The same path is exercised by backend
startup when the table is empty (see
``polyglot_alpha.api.main._maybe_auto_ingest_few_shots``); this script
remains as documentation + an operator escape hatch.

Idempotent: rows whose ``(judge_dimension, role, question_text)`` triple
already exist are skipped silently. Safe to re-run.

Usage::

    .venv/bin/python scripts/ingest_few_shots.py
"""
from __future__ import annotations

import logging
import sys

from polyglot_alpha._fewshots_seed import seed_few_shots_from_extended
from polyglot_alpha.corpus.few_shots_extended import EXTENDED_EXEMPLARS
from polyglot_alpha.persistence import init_db


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    log = logging.getLogger("ingest_few_shots")

    # Make sure tables exist on the persistence engine before we write.
    init_db()

    log.info(
        "EXTENDED_EXEMPLARS has %d entries; ingesting into few_shot_exemplars...",
        len(EXTENDED_EXEMPLARS),
    )
    inserted = seed_few_shots_from_extended(EXTENDED_EXEMPLARS)
    log.info("done: inserted=%d (existing rows were skipped)", inserted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
