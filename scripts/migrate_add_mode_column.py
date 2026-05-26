"""One-shot migration: add ``events.mode`` column + index.

W5-A1 (2026-05-26): introduces a 2-mode system for the event lifecycle:

* ``live`` (default) — real LLM, real Arc tx, real RSS, real judges.
* ``mock``           — MockLLM + ``0xsim_*`` tx hashes + canned news + judges short-circuit.

This script applies the schema delta to the local SQLite DB outside the
SQLModel ``create_all`` path because (a) the table already exists with rows
and (b) ``ALTER TABLE`` is the safe non-destructive way to add a
``NOT NULL`` column with a default. We use the stdlib ``sqlite3`` driver
directly rather than the shell ``sqlite3`` binary because the shell client
has surfaced "malformed file" errors on the WAL-mode DB during the wave3
debugging session.

The same delta is also applied at backend startup by
``polyglot_alpha.persistence.db._migrate_events_add_mode`` so fresh checkouts
do not need to run this script — it remains here as documentation + an
operator escape hatch.

Usage::

    python scripts/migrate_add_mode_column.py

Idempotent: re-running is a no-op (the helper checks ``PRAGMA table_info``
before issuing the ``ALTER TABLE``).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# Resolve the DB path relative to the repo root so the script works no
# matter where it's invoked from.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DB_PATH = _REPO_ROOT / "polyglot_alpha.db"


def main(db_path: Path = _DEFAULT_DB_PATH) -> int:
    if not db_path.exists():
        print(f"[migrate] no DB at {db_path} — nothing to do", file=sys.stderr)
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "mode" in cols:
            print("[migrate] events.mode already present — skipping ALTER")
        else:
            conn.execute(
                "ALTER TABLE events ADD COLUMN mode VARCHAR DEFAULT 'live' NOT NULL"
            )
            print("[migrate] added events.mode VARCHAR (default 'live')")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_mode ON events(mode)")
        print("[migrate] ensured idx_events_mode on events(mode)")
        conn.commit()

        # Verification pass.
        cols_after = conn.execute("PRAGMA table_info(events)").fetchall()
        mode_col = next((c for c in cols_after if c[1] == "mode"), None)
        if mode_col is None:
            print("[migrate] ERROR: mode column not visible after ALTER", file=sys.stderr)
            return 2
        print(f"[migrate] verified: {mode_col}")

        # Show distribution of mode values for confidence.
        for row in conn.execute(
            "SELECT mode, COUNT(*) FROM events GROUP BY mode"
        ).fetchall():
            print(f"[migrate] mode={row[0]!r} -> {row[1]} row(s)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
