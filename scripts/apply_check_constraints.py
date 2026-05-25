#!/usr/bin/env python3
"""Apply v0.2 DB integrity migration (CHECK constraints + index tuning).

Idempotent: safe to re-run. Detects existing constraints/indexes and
skips them.

Usage::

    .venv/bin/python scripts/apply_check_constraints.py
"""

from __future__ import annotations

import logging
import sys

from polyglot_alpha.persistence.db import engine, init_db
from polyglot_alpha.persistence.migrations.versions.m001_add_check_constraints import (
    apply,
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    log = logging.getLogger("apply_check_constraints")

    # Ensure tables exist (new installs).
    init_db()

    summary = apply(engine)
    for key, names in summary.items():
        log.info("%s (%d): %s", key, len(names), names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
