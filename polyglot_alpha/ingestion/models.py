"""Ingestion-pipeline transport types and dedup helpers.

This module previously declared its own ``Event`` and ``Source`` SQLModel
tables, but those collided with the canonical schema in
:mod:`polyglot_alpha.persistence.models` (both registered
``__tablename__ = "events"`` / ``"sources"`` on the same SQLModel
metadata). The persistence module is the source of truth for those two
tables.

What stays here:
    * ``RawEvent``, ``ConfirmedEvent`` — frozen dataclasses used as
      in-memory transport types by the watcher / cross-reference
      pipeline.
    * ``RawEntry`` — a dedup row keyed on ``(source_url, entry_id)`` —
      this table is **not** part of the persistence schema and is local
      to the ingestion DB (``polyglot_alpha.db``).
    * ``EventStatus`` — legacy enum reflecting the dispatcher's narrower
      state machine (``NEW``/``DISPATCHED``/``FAILED``/``SKIPPED``). The
      values are translated to the persistence ``EventStatus`` when the
      dispatcher writes a row.
    * ``get_engine`` — convenience helper for the SQLite watcher DB.

To avoid colliding with ``polyglot_alpha.persistence.models.SQLModel.metadata``
we declare ``RawEntry`` on its own SQLAlchemy registry/MetaData so
``SQLModel.metadata.create_all()`` over the persistence engine never
sees it (and vice-versa).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import MetaData
from sqlalchemy.orm import registry as sa_registry
from sqlmodel import Field, SQLModel, create_engine


# --------------------------------------------------------------------------- #
# Plain dataclasses used by the pipeline (in-memory transport types).         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RawEvent:
    """A single RSS entry, normalized."""

    source: str
    title: str
    summary: str
    url: str
    published_at: datetime
    language: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "published_at": self.published_at.isoformat(),
            "language": self.language,
        }


@dataclass
class ConfirmedEvent:
    """A topic confirmed by >=2 distinct sources."""

    cluster_id: str
    sources_count: int
    primary_title: str
    all_sources: list[str]  # canonical URLs
    content_hash: str
    languages: list[str] = field(default_factory=list)
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "sources_count": self.sources_count,
            "primary_title": self.primary_title,
            "all_sources": list(self.all_sources),
            "content_hash": self.content_hash,
            "languages": list(self.languages),
            "summary": self.summary,
        }


# --------------------------------------------------------------------------- #
# Legacy dispatcher status enum.                                              #
#                                                                             #
# The dispatcher writes one of these into ``persistence.models.Event.status`` #
# (which is a free-form string column). Keeping the enum here means downstream#
# code can use ``EventStatus.DISPATCHED`` without importing the persistence   #
# enum (which uses a different vocabulary).                                   #
# --------------------------------------------------------------------------- #


class EventStatus(str, enum.Enum):
    NEW = "NEW"
    DISPATCHED = "DISPATCHED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# --------------------------------------------------------------------------- #
# Isolated SQLModel registry for tables that live in the watcher DB only.    #
#                                                                             #
# Declaring ``RawEntry`` on the global ``SQLModel.metadata`` would force any  #
# ``init_db()`` call against the persistence engine to also create the       #
# ``raw_entries`` table on the wrong database. Using a private registry      #
# keeps the two schemas independent.                                          #
# --------------------------------------------------------------------------- #


_INGESTION_METADATA: MetaData = MetaData()
_INGESTION_REGISTRY: sa_registry = sa_registry(metadata=_INGESTION_METADATA)


class _IngestionBase(SQLModel, registry=_INGESTION_REGISTRY):
    """Common base so all watcher-only tables share one MetaData object."""

    pass


class RawEntry(_IngestionBase, table=True):
    """Dedup row per ``(source_url, entry_id)``."""

    __tablename__ = "raw_entries"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_url: str = Field(index=True)
    entry_id: str = Field(index=True)
    first_seen: datetime = Field(default_factory=datetime.utcnow)


# Re-export ``Event`` and ``Source`` from the persistence layer so existing
# ``from polyglot_alpha.ingestion.models import Event, Source`` callers keep
# working without holding two competing table definitions in memory.
from polyglot_alpha.persistence.models import (  # noqa: E402  (intentional late import)
    Event,
    Source,
)


# --------------------------------------------------------------------------- #
# Engine helper.                                                              #
# --------------------------------------------------------------------------- #

DEFAULT_DB_URL = "sqlite:///polyglot_alpha.db"


def get_engine(db_url: str | None = None):
    """Create (and initialize) the SQLModel engine for the watcher DB.

    Creates *both* the watcher-only tables (``raw_entries``) and the shared
    persistence tables (``events``, ``sources``, ...) so the dispatcher
    can write into either side using the same engine.
    """

    engine = create_engine(db_url or DEFAULT_DB_URL, echo=False)
    # Watcher-only tables (raw_entries).
    _INGESTION_METADATA.create_all(engine)
    # Persistence tables (events, sources, ...).
    SQLModel.metadata.create_all(engine)
    return engine


__all__ = [
    "ConfirmedEvent",
    "Event",
    "EventStatus",
    "RawEntry",
    "RawEvent",
    "Source",
    "get_engine",
]
