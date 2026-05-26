"""Concurrency stress test for the SQLite persistence layer.

Reproduces the transient ``"database disk image is malformed"`` race seen
under high concurrent triggers + concurrent reads (event 41 in
``/tmp/polyglot_backend_postdinner.log``) and asserts that the busy
timeout + retry loop added to ``polyglot_alpha.persistence.db`` keeps
the lifecycle clean.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

import pytest

from polyglot_alpha.persistence import session_scope
from polyglot_alpha.persistence.models import Event, EventStatus


_CONCURRENT_WRITERS: int = 10


def _write_one_event(idx: int) -> int:
    """Insert a single Event row inside a fresh ``session_scope``.

    Returns the assigned primary key.
    """

    with session_scope(event_id=idx) as session:
        row = Event(
            content_hash=f"stress-hash-{idx}-{threading.get_ident()}",
            sources=[{"name": "stress", "url": f"https://example.com/{idx}"}],
            language="en",
            status=EventStatus.PENDING.value,
            title=f"stress event {idx}",
        )
        session.add(row)
        session.flush()
        assert row.id is not None
        return int(row.id)


@pytest.mark.usefixtures("isolated_db")
def test_concurrent_session_scope_writes_no_database_error() -> None:
    """Fire 10 concurrent ``session_scope()`` writers; all must succeed."""

    errors: List[BaseException] = []
    ids: List[int] = []

    with ThreadPoolExecutor(max_workers=_CONCURRENT_WRITERS) as pool:
        futures = [pool.submit(_write_one_event, i) for i in range(_CONCURRENT_WRITERS)]
        for fut in as_completed(futures):
            try:
                ids.append(fut.result())
            except BaseException as exc:  # capture and assert below
                errors.append(exc)

    assert errors == [], f"Concurrent writers raised: {errors!r}"
    assert len(ids) == _CONCURRENT_WRITERS
    assert len(set(ids)) == _CONCURRENT_WRITERS, "Duplicate primary keys"


@pytest.mark.usefixtures("isolated_db")
def test_concurrent_mixed_read_write_no_database_error() -> None:
    """Interleave 10 writers + 10 readers; no DatabaseError expected."""

    errors: List[BaseException] = []

    def _read_all(_idx: int) -> int:
        from sqlmodel import select

        with session_scope() as session:
            return len(session.exec(select(Event)).all())

    with ThreadPoolExecutor(max_workers=_CONCURRENT_WRITERS * 2) as pool:
        futures = []
        for i in range(_CONCURRENT_WRITERS):
            futures.append(pool.submit(_write_one_event, 100 + i))
            futures.append(pool.submit(_read_all, i))
        for fut in as_completed(futures):
            try:
                fut.result()
            except BaseException as exc:
                errors.append(exc)

    assert errors == [], f"Mixed read/write workload raised: {errors!r}"
