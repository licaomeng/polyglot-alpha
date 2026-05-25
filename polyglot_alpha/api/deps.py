"""FastAPI dependency providers."""

from __future__ import annotations

from typing import Generator

from sqlmodel import Session

from ..persistence import get_session as _get_session
from ..pubsub import PubSub, get_pubsub


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLModel session (request-scoped)."""

    yield from _get_session()


def get_hub() -> PubSub:
    """Return the singleton pub/sub hub."""

    return get_pubsub()
