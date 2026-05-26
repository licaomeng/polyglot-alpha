"""FastAPI dependency providers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Generator, Optional

from sqlmodel import Session

from ..persistence import get_session as _get_session
from ..pubsub import PubSub, get_pubsub


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLModel session (request-scoped)."""

    yield from _get_session()


def get_hub() -> PubSub:
    """Return the singleton pub/sub hub."""

    return get_pubsub()


def utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Serialize ``dt`` as a UTC ISO-8601 string with an explicit ``Z`` suffix.

    SQLite drops timezone info on round-trip, so naive datetimes are assumed
    to already represent UTC (matching ``_utcnow`` in the persistence layer).
    Browsers parse naive ISO strings as *local* time, which produced "8h ago"
    artefacts in Singapore (UTC+8) for events created seconds earlier — this
    helper guarantees the wire format is unambiguous.
    """

    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
