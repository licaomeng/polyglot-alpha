"""Add CHECK constraints + tune indexes (v0.2 DB integrity hardening).

Revision ID: 001_add_check_constraints
Revises: (initial)
Create Date: 2026-05-26

Idempotent migration that:
  1. Adds CHECK constraints to ``bids``, ``quality_scores``,
     ``agent_reputation``, ``builder_fee_events``, ``corpus_markets``.
  2. Adds the ``ix_agent_reputation_cumulative_fees_desc`` leaderboard
     index.
  3. Drops the unused low-cardinality indexes on ``sources`` and
     ``backtest_results``.

For SQLite the constraints are added via Alembic ``batch_alter_table``
(table-rebuild). Alembic falls back to direct ``ALTER TABLE ADD
CONSTRAINT`` for Postgres.

The migration is **idempotent** — re-running is a no-op (it inspects the
schema first and skips constraints/indexes that already exist).

Can be applied either via ``alembic upgrade head`` (once env.py is wired)
**or** by invoking ``upgrade()`` directly from ``scripts/apply_check_constraints.py``
(see :func:`apply` below).
"""

from __future__ import annotations

import logging
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy.engine import Engine

# Alembic identifiers ------------------------------------------------------
revision = "001_add_check_constraints"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constraint definitions (single source of truth for both Alembic + script). #
# --------------------------------------------------------------------------- #


CHECK_CONSTRAINTS: dict[str, list[tuple[str, str]]] = {
    "bids": [
        ("bid_amount_positive_sane", "bid_amount > 0 AND bid_amount < 1000000"),
        ("agent_address_nonempty", "length(agent_address) > 0"),
    ],
    "quality_scores": [
        ("overall_score_unit", "overall_score >= 0 AND overall_score <= 1"),
        (
            "verdict_enum",
            "verdict IN ('PASS', 'FAIL', 'PENDING', 'BORDERLINE')",
        ),
    ],
    "agent_reputation": [
        ("wins_le_bids", "total_wins <= total_bids"),
        ("fees_nonneg", "cumulative_fees >= 0"),
        ("avg_quality_unit", "avg_quality >= 0 AND avg_quality <= 1"),
    ],
    "builder_fee_events": [
        ("fill_nonneg", "fill_amount >= 0"),
        (
            "fee_within_fill",
            "fee_amount >= 0 AND fee_amount <= fill_amount",
        ),
    ],
    "corpus_markets": [
        (
            "resolved_has_outcome",
            "state != 'resolved' OR outcome IS NOT NULL",
        ),
        (
            "time_order",
            "end_date IS NULL OR created_at IS NULL OR end_date >= created_at",
        ),
    ],
}


# Pre-flight: rows violating each new constraint must be quarantined
# (copied to ``<table>_quarantine``) before the rebuild can succeed.
# Keys are table names; values are the WHERE clause that selects rows
# violating ANY of the new CHECK constraints on that table. No data is
# deleted permanently — quarantined rows remain in the sidecar table for
# inspection.
DIRTY_ROW_FILTERS: dict[str, str] = {
    "bids": (
        "bid_amount <= 0 OR bid_amount >= 1000000 OR length(agent_address) = 0"
    ),
    "quality_scores": (
        "overall_score < 0 OR overall_score > 1 "
        "OR verdict NOT IN ('PASS', 'FAIL', 'PENDING', 'BORDERLINE')"
    ),
    "agent_reputation": (
        "total_wins > total_bids OR cumulative_fees < 0 "
        "OR avg_quality < 0 OR avg_quality > 1"
    ),
    "builder_fee_events": (
        "fill_amount < 0 OR fee_amount < 0 OR fee_amount > fill_amount"
    ),
    "corpus_markets": (
        "(state = 'resolved' AND outcome IS NULL) "
        "OR (end_date IS NOT NULL AND created_at IS NOT NULL "
        "AND end_date < created_at)"
    ),
}


# Indexes to drop (DB integrity report: unused low-cardinality indexes).
INDEXES_TO_DROP: list[tuple[str, str]] = [
    ("ix_sources_language", "sources"),
    ("ix_sources_status", "sources"),
    ("ix_backtest_results_judge_verdict", "backtest_results"),
    ("ix_backtest_results_backtested_at", "backtest_results"),
]


# Indexes to create (idempotent).
INDEXES_TO_CREATE: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "ix_agent_reputation_cumulative_fees_desc",
        "agent_reputation",
        ("cumulative_fees",),
    ),
]


# --------------------------------------------------------------------------- #
# Alembic entry points (used when env.py is wired).                           #
# --------------------------------------------------------------------------- #


def upgrade() -> None:
    """Alembic-driven upgrade (uses ``op.batch_alter_table`` for SQLite)."""

    from alembic import op  # noqa: WPS433 — lazy import keeps script-mode usable

    bind = op.get_bind()

    for table, constraints in CHECK_CONSTRAINTS.items():
        with op.batch_alter_table(table) as batch_op:
            for name, expr in constraints:
                if _constraint_already_present(bind, table, name):
                    continue
                batch_op.create_check_constraint(name, expr)

    for name, table in INDEXES_TO_DROP:
        if _index_exists(bind, table, name):
            op.drop_index(name, table_name=table)

    for name, table, cols in INDEXES_TO_CREATE:
        if not _index_exists(bind, table, name):
            op.create_index(name, table, list(cols))


def downgrade() -> None:
    """Reverse the upgrade (drop constraints + restore indexes)."""

    from alembic import op  # noqa: WPS433

    for table, constraints in CHECK_CONSTRAINTS.items():
        with op.batch_alter_table(table) as batch_op:
            for name, _expr in constraints:
                batch_op.drop_constraint(name, type_="check")

    for name, table, cols in INDEXES_TO_CREATE:
        op.drop_index(name, table_name=table)

    for name, table in INDEXES_TO_DROP:
        op.create_index(name, table, [_index_col_for(name)])


# --------------------------------------------------------------------------- #
# Script-mode entry point (no Alembic env.py required).                       #
# --------------------------------------------------------------------------- #


def apply(engine: Engine) -> dict[str, list[str]]:
    """Run the upgrade against ``engine`` without Alembic plumbing.

    Returns a dict of applied/skipped operations for logging. Safe to
    re-run — every operation is gated on a schema-introspection check.
    """

    applied: dict[str, list[str]] = {
        "constraints_added": [],
        "constraints_skipped": [],
        "indexes_dropped": [],
        "indexes_created": [],
        "rows_quarantined": [],
    }

    dialect = engine.dialect.name

    with engine.begin() as conn:
        # Move dirty rows out of the way BEFORE attempting the rebuild —
        # otherwise the CHECK on the new table will reject the copy.
        _quarantine_dirty_rows(conn, applied)

        if dialect == "sqlite":
            _apply_sqlite(conn, applied)
        else:
            _apply_generic(conn, applied)

        # Index ops are dialect-agnostic.
        for name, table in INDEXES_TO_DROP:
            if _index_exists(conn, table, name):
                conn.execute(sa.text(f"DROP INDEX IF EXISTS {name}"))
                applied["indexes_dropped"].append(name)

        for name, table, cols in INDEXES_TO_CREATE:
            if not _index_exists(conn, table, name):
                col_list = ", ".join(cols)
                conn.execute(
                    sa.text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({col_list})")
                )
                applied["indexes_created"].append(name)

    return applied


# --------------------------------------------------------------------------- #
# Internals.                                                                  #
# --------------------------------------------------------------------------- #


def _quarantine_dirty_rows(
    conn: sa.Connection, applied: dict[str, list[str]]
) -> None:
    """Move rows violating the new CHECK constraints into ``<table>_quarantine``.

    No data is destroyed — quarantined rows live in a sidecar table named
    ``<table>_quarantine`` (created on first run) so an operator can
    inspect them. Re-running the migration is a no-op once the source
    table is clean.
    """

    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    for table, where in DIRTY_ROW_FILTERS.items():
        if table not in existing_tables:
            continue

        # Count violators first — skip the table entirely if clean.
        n = conn.execute(
            sa.text(f"SELECT COUNT(*) FROM {table} WHERE {where}")
        ).scalar_one()
        if not n:
            continue

        quarantine = f"{table}_quarantine"
        if quarantine not in existing_tables:
            conn.execute(
                sa.text(
                    f"CREATE TABLE {quarantine} AS "
                    f"SELECT * FROM {table} WHERE 0"
                )
            )

        conn.execute(
            sa.text(
                f"INSERT INTO {quarantine} SELECT * FROM {table} WHERE {where}"
            )
        )
        conn.execute(sa.text(f"DELETE FROM {table} WHERE {where}"))
        applied["rows_quarantined"].append(f"{table}={n}")
        logger.warning(
            "quarantined %d rows from %s into %s (violate new CHECK)",
            n,
            table,
            quarantine,
        )


def _apply_sqlite(conn: sa.Connection, applied: dict[str, list[str]]) -> None:
    """SQLite path: rebuild each table to add CHECK constraints.

    SQLite < 3.35 does not support ``ALTER TABLE ADD CONSTRAINT``. The
    canonical workaround is the 12-step table rebuild documented at
    https://www.sqlite.org/lang_altertable.html (rename → create new →
    copy → drop old → rename). We use a simplified variant: read the
    existing ``CREATE TABLE`` SQL, splice the constraints in, and rebuild.

    To keep the implementation small, we instead use SQLModel's metadata
    to rebuild the table from the model definitions — but only after
    verifying the table is missing the target constraint(s).
    """

    from polyglot_alpha.persistence import models  # noqa: WPS433

    inspector = sa.inspect(conn)
    metadata = models.SQLModel.metadata

    for table_name, constraints in CHECK_CONSTRAINTS.items():
        existing_sql = _sqlite_table_sql(conn, table_name) or ""
        missing = [
            (name, expr)
            for name, expr in constraints
            if f"CONSTRAINT {name}" not in existing_sql
            and f'"{name}"' not in existing_sql
        ]
        if not missing:
            applied["constraints_skipped"].extend(name for name, _ in constraints)
            continue

        if table_name not in inspector.get_table_names():
            # Table doesn't exist yet — create_all() will pick up the
            # constraints from the model __table_args__ on first init.
            continue

        _sqlite_rebuild_table(conn, metadata, table_name)
        applied["constraints_added"].extend(name for name, _ in missing)


def _apply_generic(conn: sa.Connection, applied: dict[str, list[str]]) -> None:
    """Postgres / generic path: ``ALTER TABLE ADD CONSTRAINT``."""

    for table_name, constraints in CHECK_CONSTRAINTS.items():
        for name, expr in constraints:
            if _constraint_already_present(conn, table_name, name):
                applied["constraints_skipped"].append(name)
                continue
            conn.execute(
                sa.text(
                    f"ALTER TABLE {table_name} "
                    f"ADD CONSTRAINT {name} CHECK ({expr})"
                )
            )
            applied["constraints_added"].append(name)


def _sqlite_table_sql(conn: sa.Connection, table_name: str) -> str | None:
    row = conn.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table_name},
    ).fetchone()
    return row[0] if row else None


def _sqlite_rebuild_table(
    conn: sa.Connection, metadata: sa.MetaData, table_name: str
) -> None:
    """Rebuild a SQLite table from its SQLModel metadata definition.

    Steps (per https://www.sqlite.org/lang_altertable.html):
      1. ``PRAGMA foreign_keys=OFF``
      2. Create the new table under a temp name from current metadata.
      3. Copy all rows from the old table (column intersection only).
      4. Drop the old table.
      5. Rename the new table.
      6. Re-create indexes (SQLAlchemy ``create_all`` handles this).
      7. ``PRAGMA foreign_keys=ON``
    """

    target = metadata.tables[table_name]
    new_name = f"_{table_name}_new"
    cols = ", ".join(c.name for c in target.columns)

    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        # Drop any stale temp table from a previous failed run.
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {new_name}"))

        # Create the new table with constraints by rendering DDL with a
        # different name. SQLAlchemy's compile path supports a temporary
        # override via Table.tometadata + name change, but the simplest
        # cross-version approach is to compile the CREATE TABLE manually.
        ddl = str(
            sa.schema.CreateTable(target).compile(dialect=conn.dialect)
        ).strip()
        # Splice in the new name (target table SQL still uses original
        # name — rebuild it for the temp).
        ddl_new = ddl.replace(f"CREATE TABLE {table_name}", f"CREATE TABLE {new_name}", 1)
        conn.execute(sa.text(ddl_new))

        # Copy rows.
        conn.execute(
            sa.text(f"INSERT INTO {new_name} ({cols}) SELECT {cols} FROM {table_name}")
        )

        # Capture indexes on the old table to recreate (drop-on-rebuild loses them).
        old_indexes = list(
            conn.execute(
                sa.text(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type='index' AND tbl_name=:t AND sql IS NOT NULL"
                ),
                {"t": table_name},
            )
        )

        conn.execute(sa.text(f"DROP TABLE {table_name}"))
        conn.execute(sa.text(f"ALTER TABLE {new_name} RENAME TO {table_name}"))

        # Re-create explicit indexes (PK/unique are folded into the new DDL).
        for idx_name, idx_sql in old_indexes:
            if idx_name.startswith("sqlite_autoindex"):
                continue
            if not idx_sql:
                continue
            # The captured SQL still references the original table name (correct).
            conn.execute(sa.text(idx_sql))
    finally:
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))


def _constraint_already_present(
    bind: sa.Connection | Engine, table: str, name: str
) -> bool:
    """Best-effort check across dialects."""

    dialect = bind.dialect.name
    if dialect == "sqlite":
        with _maybe_connect(bind) as conn:
            sql = _sqlite_table_sql(conn, table) or ""
        return f"CONSTRAINT {name}" in sql or f'"{name}"' in sql

    # Postgres / generic: query information_schema.
    with _maybe_connect(bind) as conn:
        row = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name=:t AND constraint_name=:n"
            ),
            {"t": table, "n": name},
        ).fetchone()
    return row is not None


def _index_exists(bind: sa.Connection | Engine, table: str, name: str) -> bool:
    with _maybe_connect(bind) as conn:
        if conn.dialect.name == "sqlite":
            row = conn.execute(
                sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:n"),
                {"n": name},
            ).fetchone()
        else:
            row = conn.execute(
                sa.text("SELECT 1 FROM pg_indexes WHERE indexname=:n"),
                {"n": name},
            ).fetchone()
    return row is not None


def _index_col_for(name: str) -> str:
    # Best-effort name → column mapping for downgrade restoration.
    mapping = {
        "ix_sources_language": "language",
        "ix_sources_status": "status",
        "ix_backtest_results_judge_verdict": "judge_verdict",
        "ix_backtest_results_backtested_at": "backtested_at",
    }
    return mapping[name]


class _MaybeConnect:
    """Adapter that yields a ``Connection`` whether given an Engine or
    Connection (used so the helpers above work in both contexts)."""

    def __init__(self, bind: sa.Connection | Engine) -> None:
        self._bind = bind
        self._owned: sa.Connection | None = None

    def __enter__(self) -> sa.Connection:
        if isinstance(self._bind, Engine):
            self._owned = self._bind.connect()
            return self._owned
        return self._bind

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._owned is not None:
            self._owned.close()


def _maybe_connect(bind: sa.Connection | Engine) -> _MaybeConnect:
    return _MaybeConnect(bind)


def _names(items: Iterable[tuple[str, str]]) -> list[str]:
    return [n for n, _ in items]


if __name__ == "__main__":  # pragma: no cover — script-mode entry
    from polyglot_alpha.persistence.db import engine

    logging.basicConfig(level=logging.INFO)
    summary = apply(engine)
    for key, names in summary.items():
        logger.info("%s: %s", key, names)
