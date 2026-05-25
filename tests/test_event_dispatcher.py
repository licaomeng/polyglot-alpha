"""Event dispatcher tests (no chain — chain client mocked)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from polyglot_alpha.ingestion.cross_reference import content_hash
from polyglot_alpha.ingestion.event_dispatcher import (
    EventDispatcher,
    _hash_to_bytes32,
    _load_demo_samples,
    run_demo,
)
from polyglot_alpha.ingestion.models import (
    ConfirmedEvent,
    Event,
    EventStatus,
    get_engine,
)


@pytest.fixture
def engine(tmp_path: Path):
    return get_engine(f"sqlite:///{tmp_path / 'dispatch.db'}")


def _make_event(title: str = "PBOC cuts RRR") -> ConfirmedEvent:
    urls = ["https://caixin.com/a", "https://xinhua.com/b"]
    return ConfirmedEvent(
        cluster_id="c0",
        sources_count=2,
        primary_title=title,
        all_sources=urls,
        content_hash=content_hash(title, urls),
        languages=["zh", "en"],
        summary="confirmed",
    )


class FakeChain:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, bytes]] = []

    def open_auction(self, event_id: bytes, event_hash: bytes) -> str:
        self.calls.append((event_id, event_hash))
        return "0x" + "ab" * 32


def test_hash_to_bytes32_pads_and_truncates() -> None:
    short = _hash_to_bytes32("abcd")
    assert len(short) == 32
    assert short.endswith(bytes.fromhex("abcd"))

    long = _hash_to_bytes32("aa" * 40)
    assert len(long) == 32
    assert long == bytes.fromhex("aa" * 32)


@pytest.mark.asyncio
async def test_dispatch_records_and_calls_chain(engine) -> None:
    chain = FakeChain()
    dispatcher = EventDispatcher(engine=engine, chain=chain)
    ev = _make_event()

    row = await dispatcher.dispatch(ev)
    assert row is not None
    assert row.status == EventStatus.DISPATCHED
    assert row.tx_hash and row.tx_hash.startswith("0x")
    assert len(chain.calls) == 1

    with Session(engine) as session:
        rows = session.exec(select(Event)).all()
        assert len(rows) == 1
        assert rows[0].content_hash == ev.content_hash


@pytest.mark.asyncio
async def test_dispatch_respects_dedup_window(engine) -> None:
    chain = FakeChain()
    dispatcher = EventDispatcher(engine=engine, chain=chain)
    ev = _make_event()
    first = await dispatcher.dispatch(ev)
    second = await dispatcher.dispatch(ev)

    assert first is not None
    assert second is None  # within window -> skipped
    assert len(chain.calls) == 1


@pytest.mark.asyncio
async def test_dispatch_failure_marks_failed(engine) -> None:
    class ExplodingChain:
        def open_auction(self, *_args, **_kw):
            raise RuntimeError("RPC down")

    dispatcher = EventDispatcher(engine=engine, chain=ExplodingChain())
    row = await dispatcher.dispatch(_make_event())
    assert row is not None
    assert row.status == EventStatus.FAILED
    assert row.tx_hash is None


@pytest.mark.asyncio
async def test_dispatch_without_chain_records_only(engine) -> None:
    dispatcher = EventDispatcher(engine=engine, chain=None)
    row = await dispatcher.dispatch(_make_event("Different title for unique hash"))
    assert row is not None
    assert row.status == EventStatus.NEW


def test_load_demo_samples_reads_outputs_dir() -> None:
    outputs_dir = Path(__file__).resolve().parents[1] / "outputs"
    samples = _load_demo_samples(outputs_dir)
    assert len(samples) == 5
    titles = [s.primary_title for s in samples]
    assert any("People's Bank" in t or "PBOC" in t for t in titles)
    for s in samples:
        assert len(s.content_hash) == 64
        assert s.sources_count >= 2


@pytest.mark.asyncio
async def test_run_demo_dispatches_with_zero_interval(engine, tmp_path: Path, monkeypatch) -> None:
    """End-to-end demo run with chain mocked and dedup table empty."""

    db_url = f"sqlite:///{tmp_path / 'demo.db'}"
    monkeypatch.setenv("POLYGLOT_DB", db_url)

    from polyglot_alpha.ingestion import event_dispatcher as ed

    monkeypatch.setattr(ed, "get_engine", lambda url=None: get_engine(db_url))

    outputs_dir = Path(__file__).resolve().parents[1] / "outputs"
    dispatched = await run_demo(outputs_dir, interval_seconds=0.0, use_chain=False)
    assert len(dispatched) == 5
    assert all(row.status == EventStatus.NEW for row in dispatched)
