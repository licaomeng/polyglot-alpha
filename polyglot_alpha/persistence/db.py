"""Database engine + session factory.

Defaults to SQLite at ``polyglot_alpha.db``. Override with ``DATABASE_URL``
env var (e.g. ``postgresql+psycopg://user:pwd@host:5432/db``).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError, OperationalError
from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger(__name__)

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "sqlite:///./polyglot_alpha.db",
)


# SQLite PRAGMA tuning constants (WAL journal recommendations).
# Bumped 10s from 5s after observing transient "disk image is malformed"
# under concurrent writer commit + reader snapshot in WAL mode (event 41).
_SQLITE_BUSY_TIMEOUT_MS: int = 10000
_SQLITE_WAL_AUTOCHECKPOINT_PAGES: int = 1000

# Retry policy for transient SQLite errors ("malformed" / "locked" / "busy")
# that the WAL self-heals but surfaces a one-shot error to the caller.
_SQLITE_RETRY_MAX_ATTEMPTS: int = 3
_SQLITE_RETRY_BACKOFFS_SEC: tuple[float, ...] = (0.05, 0.2, 0.8)
_SQLITE_TRANSIENT_ERROR_MARKERS: tuple[str, ...] = (
    "malformed",
    "database is locked",
    "database disk image is malformed",
)


def _is_transient_sqlite_error(exc: BaseException) -> bool:
    """Return True if the exception looks like a recoverable WAL race."""

    if not isinstance(exc, (DatabaseError, OperationalError, sqlite3.DatabaseError)):
        return False
    msg = str(exc).lower()
    return any(marker in msg for marker in _SQLITE_TRANSIENT_ERROR_MARKERS)


def _register_sqlite_pragmas(engine: Engine) -> None:
    """Apply WAL journal mode + recommended PRAGMAs on every new connection.

    No-op for non-sqlite dialects (Postgres etc).

    Pragmas set:
      * ``journal_mode=WAL``       — concurrent readers + single writer
      * ``busy_timeout=10000``     — wait up to 10s for a lock instead of
                                      immediately raising ``OperationalError``
      * ``synchronous=NORMAL``     — standard durable+fast combo with WAL
      * ``wal_autocheckpoint=1000`` — checkpoint after 1000 pages so the
                                      WAL file does not grow unbounded
      * ``temp_store=MEMORY``      — keep temp tables in memory
    """

    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn: object, _conn_record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute(
                f"PRAGMA wal_autocheckpoint={_SQLITE_WAL_AUTOCHECKPOINT_PAGES}"
            )
            cursor.execute("PRAGMA temp_store=MEMORY")
        finally:
            cursor.close()


def _make_engine(url: str) -> Engine:
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # Allow shared use from FastAPI + background tasks.
        connect_args = {"check_same_thread": False}
    new_engine = create_engine(url, echo=False, connect_args=connect_args)
    _register_sqlite_pragmas(new_engine)
    return new_engine


engine: Engine = _make_engine(DATABASE_URL)


def init_db() -> None:
    """Create all tables (no-op if they exist). Imports models so SQLModel
    metadata is registered before ``create_all`` runs."""

    from . import models  # noqa: F401  side-effect: register tables

    SQLModel.metadata.create_all(engine)
    _migrate_polymarket_submissions(engine)


# Columns added to ``polymarket_submissions`` after the table's first
# release (2026-05-26). Each entry is ``(column_name, ddl_type)``. The
# helper below is idempotent — re-running is a no-op — and applies
# ``ALTER TABLE ADD COLUMN`` only for columns that are missing. This
# avoids destroying the ~76 historical submission rows already in the
# local SQLite DB while still bringing the schema in line with the
# updated ``PolymarketSubmission`` model.
_POLYMARKET_SUBMISSION_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("mode", "VARCHAR"),
    ("fees_estimate_usdc", "FLOAT"),
    ("payload", "JSON"),
)


def _migrate_polymarket_submissions(engine: Engine) -> None:
    """Idempotently add new columns to ``polymarket_submissions``.

    Works for both SQLite (``ALTER TABLE ADD COLUMN``) and Postgres
    (same DDL syntax). Skipped silently if the table doesn't exist yet
    (fresh DB — ``create_all`` will have rendered the full schema).
    """

    from sqlalchemy import inspect as sa_inspect, text as sa_text

    try:
        inspector = sa_inspect(engine)
        if "polymarket_submissions" not in inspector.get_table_names():
            return
        existing_cols = {
            c["name"] for c in inspector.get_columns("polymarket_submissions")
        }
        missing = [
            (name, ddl)
            for name, ddl in _POLYMARKET_SUBMISSION_NEW_COLUMNS
            if name not in existing_cols
        ]
        if not missing:
            return
        with engine.begin() as conn:
            for name, ddl in missing:
                conn.execute(
                    sa_text(
                        f"ALTER TABLE polymarket_submissions ADD COLUMN {name} {ddl}"
                    )
                )
                logger.info(
                    "migrated polymarket_submissions: added column %s %s",
                    name,
                    ddl,
                )
    except Exception as exc:  # noqa: BLE001 — best-effort, keep startup alive
        logger.warning(
            "polymarket_submissions migration skipped (%s)", exc
        )


def reset_engine(url: str) -> Engine:
    """Rebuild the module-level engine. Used by tests + Postgres switch."""

    global engine, DATABASE_URL
    DATABASE_URL = url
    engine = _make_engine(url)
    return engine


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a SQLModel session."""

    with Session(engine) as session:
        yield session


@contextmanager
def session_scope(event_id: int | None = None) -> Generator[Session, None, None]:
    """Context-manager session for orchestrator / background tasks.

    Wraps the ``commit`` in a bounded retry loop (max 3 attempts with
    50ms / 200ms / 800ms exponential backoff) that catches transient
    SQLite errors observed under concurrent WAL writer+reader races
    ("disk image is malformed", "database is locked"). Non-transient
    errors are re-raised immediately.

    ``event_id`` is an optional correlation id logged on every retry so
    operators can grep ``[event_id=N]`` in the backend log.
    """

    session = Session(engine)
    correlation = f"[event_id={event_id}]" if event_id is not None else ""
    try:
        yield session

        # ---- bounded retry on commit only (yield body already ran) ----
        last_exc: BaseException | None = None
        for attempt in range(_SQLITE_RETRY_MAX_ATTEMPTS):
            try:
                session.commit()
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001 — classified below
                last_exc = exc
                if not _is_transient_sqlite_error(exc):
                    session.rollback()
                    raise
                # Transient: rollback, sleep, retry.
                try:
                    session.rollback()
                except Exception:  # rollback itself may flake — ignore
                    pass
                if attempt + 1 >= _SQLITE_RETRY_MAX_ATTEMPTS:
                    logger.error(
                        "%s sqlite commit failed after %d attempts: %s",
                        correlation,
                        _SQLITE_RETRY_MAX_ATTEMPTS,
                        exc,
                    )
                    raise
                backoff = _SQLITE_RETRY_BACKOFFS_SEC[attempt]
                logger.warning(
                    "%s sqlite transient error on commit (attempt %d/%d), "
                    "retrying in %.3fs: %s",
                    correlation,
                    attempt + 1,
                    _SQLITE_RETRY_MAX_ATTEMPTS,
                    backoff,
                    exc,
                )
                time.sleep(backoff)
        if last_exc is not None:
            raise last_exc
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    finally:
        session.close()
