"""Database engine + session factory.

Defaults to SQLite at ``polyglot_alpha.db``. Override with ``DATABASE_URL``
env var (e.g. ``postgresql+psycopg://user:pwd@host:5432/db``).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "sqlite:///./polyglot_alpha.db",
)


# SQLite PRAGMA tuning constants (WAL journal recommendations).
_SQLITE_BUSY_TIMEOUT_MS: int = 5000


def _register_sqlite_pragmas(engine: Engine) -> None:
    """Apply WAL journal mode + recommended PRAGMAs on every new connection.

    No-op for non-sqlite dialects (Postgres etc).
    """

    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn: object, _conn_record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
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
def session_scope() -> Generator[Session, None, None]:
    """Context-manager session for orchestrator / background tasks."""

    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
