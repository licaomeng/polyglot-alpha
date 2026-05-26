"""E2E tests for stuck-event recovery and duplicate-event dedup.

Three scenarios:

1. ``test_event_stuck_in_evaluating_recovered_on_next_start`` — manually
   create a row in EVALUATING, restart the app, expect a recovery sweep
   to mark it FAILED. **As of 2026-05-26 no such sweep exists in the
   lifespan**, so this test is XFAIL-marked with a gap reference in
   outputs/E3_test_findings.md.
2. ``test_event_stuck_in_auction_open_after_window`` — past auction
   window with no bids; assert FAILED. The orchestrator's no-bid path
   marks the event FAILED with reason='no_bids' synchronously, so we
   exercise that here.
3. ``test_duplicate_event_id_collision_dedup`` — 2 concurrent triggers
   with the same content_hash. Only one progresses; the other dedups.

Tests use the in-process FastAPI app + isolated SQLite.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from sqlmodel import Session, select


@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("POLYGLOT_LLM_BACKEND", "mock")


@pytest.fixture(autouse=True)
def _force_judges_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the judge panel to PASS so the lifecycle terminates quickly."""

    from polyglot_alpha import orchestrator

    async def passing(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.9},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.92,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing)


def _build_app() -> Any:
    from polyglot_alpha.api.main import create_app

    return create_app()


# ---------------------------------------------------------------------------
# 1. Event stuck in EVALUATING — recovery sweep gap.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "No automated recovery sweep exists in the FastAPI lifespan. "
        "Documented gap in outputs/E3_test_findings.md."
    ),
    strict=False,
)
def test_event_stuck_in_evaluating_recovered_on_next_start(
    isolated_db: str,
) -> None:
    """Manually create a row in EVALUATING; assert a startup sweep reverts it.

    Documented behavior we EXPECT after the recovery sweep lands:
      * On lifespan startup, any Event row in a non-terminal status
        older than the lifecycle SLA (60-90 s) is reset to FAILED with
        reason='startup_recovery'.

    Today (2026-05-26) the api/main.py lifespan only calls ``init_db``
    + ``get_pubsub`` — no recovery sweep. This test is XFAIL'd until
    the sweep is implemented (tracked as a finding by E3).
    """

    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import Event, EventStatus

    # Seed an EVALUATING row that "predates" the restart.
    with Session(engine) as s:
        ev = Event(
            content_hash="stuck-eval-" + "a" * 50,
            sources=[{"url": "https://example.com/stuck"}],
            language="en",
            title="Stuck in EVALUATING",
            status=EventStatus.EVALUATING.value,
            triggered_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=10),
        )
        s.add(ev)
        s.commit()
        stuck_id = ev.id

    # "Restart" the app by constructing a new FastAPI instance — this
    # runs the lifespan hook again.
    app = _build_app()
    from fastapi.testclient import TestClient

    with TestClient(app) as _client:
        # Lifespan startup has fired by now.
        pass

    # Expected post-sweep state: FAILED. This will fail until the sweep
    # is wired into the lifespan.
    with Session(engine) as s:
        row = s.get(Event, stuck_id)
        assert row is not None
        assert row.status == EventStatus.FAILED.value


# ---------------------------------------------------------------------------
# 2. Auction window expired with no bids -> FAILED (synchronous path).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_stuck_in_auction_open_after_window(
    isolated_db: str,
) -> None:
    """An auction with zero bids past its window is finalized as FAILED.

    The orchestrator's mock_bids=[] fast-path explicitly marks the event
    FAILED with reason='no_bids' — that's the documented behavior for
    the "auction window expired" case. We assert the event row reaches
    FAILED and that no Translation / QualityScore rows ever existed.
    """

    from polyglot_alpha.orchestrator import run_lifecycle
    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import (
        Bid,
        Event,
        EventStatus,
        QualityScore,
        Translation,
    )

    result = await run_lifecycle(
        {
            "title": "Auction window expired",
            "sources": [{"url": "https://example.com/expire"}],
            "language": "en",
        },
        # auction_window_seconds=0 simulates "window has expired
        # immediately" — the same code path the real lifecycle hits
        # after a real auction with no bids.
        auction_window_seconds=0.0,
        mock_bids=[],
    )

    assert result["status"] == EventStatus.FAILED.value
    assert result.get("reason") == "no_bids"

    with Session(engine) as s:
        row = s.get(Event, result["event_id"])
        assert row is not None
        assert row.status == EventStatus.FAILED.value
        # No downstream rows populated.
        assert s.exec(select(Bid).where(Bid.event_id == row.id)).first() is None
        assert (
            s.exec(select(Translation).where(Translation.event_id == row.id)).first()
            is None
        )
        assert (
            s.exec(
                select(QualityScore).where(QualityScore.event_id == row.id)
            ).first()
            is None
        )


# ---------------------------------------------------------------------------
# 3. Duplicate-content_hash collision: only one progresses.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_event_id_collision_dedup(
    isolated_db: str,
) -> None:
    """Two near-simultaneous triggers with identical content -> exactly one row.

    The trigger route has two layers of dedup:
      * 5-minute sliding window on identical title.
      * Permanent ``content_hash`` dedup inside ``create_pending_event``.

    Either layer is sufficient to ensure only one Event row exists for
    the same payload. We POST twice in quick succession and assert:
      * Both responses 200.
      * Same event_id in both responses.
      * Exactly one Event row in the database.
    """

    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import Event

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    payload = {
        "title": "Duplicate content_hash collision test",
        "sources": [{"name": "src", "url": "https://example.com/dupe"}],
        "language": "en",
        "auction_window_seconds": 0.0,
        "mock_bids": [
            {"agent_address": "0xdupe_a", "bid_amount": 1.0},
            {"agent_address": "0xdupe_b", "bid_amount": 3.0},
        ],
    }

    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        # Fire concurrently to maximise the chance of a true race.
        r1_task = asyncio.create_task(client.post("/trigger/event", json=payload))
        r2_task = asyncio.create_task(client.post("/trigger/event", json=payload))
        r1, r2 = await asyncio.gather(r1_task, r2_task)

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    body1 = r1.json()
    body2 = r2.json()
    # Both response bodies must point at the same event_id.
    assert body1["event_id"] == body2["event_id"], (
        f"dedup failed: got {body1['event_id']} vs {body2['event_id']}"
    )
    # At least one of the responses must be flagged as deduped.
    assert (body1.get("deduped") is True) or (body2.get("deduped") is True), (
        "neither response marked deduped: "
        f"r1={body1!r} r2={body2!r}"
    )

    # Database should only hold one row for this content.
    with Session(engine) as s:
        rows = s.exec(
            select(Event).where(
                Event.title == "Duplicate content_hash collision test"
            )
        ).all()
        assert len(rows) == 1, (
            f"expected 1 Event row after dedup; got {len(rows)} "
            f"(ids={[r.id for r in rows]})"
        )
