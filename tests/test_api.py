"""Tests for the FastAPI app + every route."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient


def _build_app():
    from polyglot_alpha.api.main import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _force_judges_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the judge panel to PASS so demo lifecycles complete to SUBMITTED."""

    from polyglot_alpha import orchestrator

    async def passing_judges(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.9, "comet": 0.85, "mqm": {"score": 0}},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.92,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing_judges)


def test_health_and_root(isolated_db: str) -> None:
    app = _build_app()
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["name"] == "polyglot-alpha"


def test_trigger_runs_full_lifecycle_and_returns_summary(isolated_db: str) -> None:
    app = _build_app()
    with TestClient(app) as client:
        payload: dict[str, Any] = {
            "title": "Trigger test event",
            "sources": [{"name": "test", "url": "https://example.com/x"}],
            "auction_window_seconds": 0.0,
            "mock_bids": [
                # Thesis: lowest qualified bid wins. ``0xwinner`` bids
                # the lower amount, so it is the auction winner.
                {"agent_address": "0xwinner", "bid_amount": 2.0},
                {"agent_address": "0xrunner", "bid_amount": 5.0},
            ],
        }
        r = client.post("/trigger/event", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "SUBMITTED"
        assert body["winner_address"] == "0xwinner"
        assert body["is_simulated"] is True


def test_events_routes_list_get_and_bids(isolated_db: str) -> None:
    app = _build_app()
    with TestClient(app) as client:
        client.post(
            "/trigger/event",
            json={
                "title": "Events route test",
                "sources": [],
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    {"agent_address": "0xA", "bid_amount": 1.0},
                    {"agent_address": "0xB", "bid_amount": 3.0},
                ],
            },
        )
        r = client.get("/events")
        assert r.status_code == 200
        events = r.json()
        assert isinstance(events, list)
        assert len(events) == 1
        event_id = events[0]["id"]

        r = client.get(f"/events/{event_id}")
        assert r.status_code == 200
        assert str(r.json()["id"]) == str(event_id)

        r = client.get(f"/events/{event_id}/bids")
        assert r.status_code == 200
        bids = r.json()["items"]
        assert len(bids) == 2
        assert {b["agent_address"] for b in bids} == {"0xA", "0xB"}

        r = client.get("/events/99999")
        assert r.status_code == 404


def test_agents_routes(isolated_db: str) -> None:
    app = _build_app()
    with TestClient(app) as client:
        client.post(
            "/trigger/event",
            json={
                "title": "Agents route test",
                "sources": [],
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    # Lowest qualified bid wins.
                    {"agent_address": "0xWINNER", "bid_amount": 1.0},
                    {"agent_address": "0xLOSER", "bid_amount": 4.0},
                ],
            },
        )
        r = client.get("/agents/0xWINNER")
        assert r.status_code == 200
        body = r.json()
        assert body["agent_address"] == "0xWINNER"
        assert body["total_wins"] == 1

        r = client.get("/agents/0xWINNER/history")
        assert r.status_code == 200
        history = r.json()
        assert history["wins"], "winner should have at least one win"
        assert history["bids"], "winner should have at least one bid"

        r = client.get("/agents/0xUNKNOWN")
        assert r.status_code == 404


def test_leaderboard_route(isolated_db: str) -> None:
    app = _build_app()
    with TestClient(app) as client:
        client.post(
            "/trigger/event",
            json={
                "title": "Leaderboard run A",
                "sources": [],
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    # Lowest qualified bid wins -> 0xTOP (bid=1.0).
                    {"agent_address": "0xTOP", "bid_amount": 1.0},
                    {"agent_address": "0xMID", "bid_amount": 9.0},
                ],
            },
        )
        client.post(
            "/trigger/event",
            json={
                "title": "Leaderboard run B",
                "sources": [],
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    {"agent_address": "0xTOP", "bid_amount": 7.0},
                ],
            },
        )
        r = client.get("/leaderboard?sort_by=cumulative_fees")
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        assert items[0]["agent_address"] == "0xTOP"
        assert items[0]["address"] == "0xTOP"
        assert items[0]["rank"] == 1

        r = client.get("/leaderboard?sort_by=total_wins&limit=5")
        assert r.status_code == 200


def test_trigger_validation_errors(isolated_db: str) -> None:
    app = _build_app()
    with TestClient(app) as client:
        # Missing title -> 422
        r = client.post("/trigger/event", json={})
        assert r.status_code == 422


def test_trigger_event_source_rss_no_title_returns_200(
    isolated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``event_source='rss'`` with no title must succeed even when the live
    RSS pipeline is unavailable — it falls back to the hardcoded sample.
    """

    # Force the RSS fetch helper to return None so we exercise the
    # ``hardcoded`` -> in-process fallback chain.
    from polyglot_alpha.api.routes import trigger as trigger_route

    async def fake_rss_fetch(_window: int) -> None:
        return None

    monkeypatch.setattr(trigger_route, "_fetch_rss_demo_event", fake_rss_fetch)

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/trigger/event",
            json={
                "event_source": "rss",
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    {"agent_address": "0xrss", "bid_amount": 1.0}
                ],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "SUBMITTED"
        # The synthesized title should be a non-empty string (either the
        # hardcoded sample or the baked-in fallback headline).
        assert isinstance(body.get("event_id"), int)


def test_trigger_event_source_hardcoded_no_title_returns_200(
    isolated_db: str,
) -> None:
    """``event_source='hardcoded'`` loads ``outputs/sample_0.json``."""

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/trigger/event",
            json={
                "event_source": "hardcoded",
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    {"agent_address": "0xhardcoded", "bid_amount": 1.0}
                ],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUBMITTED"


@pytest.mark.asyncio
async def test_sse_iterator_emits_lifecycle_events(isolated_db: str) -> None:
    """Drive the SSE iterator directly + run a lifecycle; assert events arrive.

    Note: We test the SSE iterator function rather than streaming over
    httpx's ASGITransport because ASGITransport buffers streamed bodies
    until response completion, which makes long-lived SSE responses
    untestable through the transport. The route function uses this iterator
    verbatim, so the contract is preserved.
    """

    import asyncio

    from polyglot_alpha.api.routes.sse import _event_iter
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle

    class _FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    seen: list[dict[str, Any]] = []

    async def reader() -> None:
        async for msg in _event_iter(_FakeRequest()):  # type: ignore[arg-type]
            seen.append(msg)
            if msg.get("event") == "polymarket.submitted":
                return

    reader_task = asyncio.create_task(reader())
    await asyncio.sleep(0.1)  # let the subscriber register

    await run_lifecycle(
        {
            "title": "SSE stream test",
            "sources": [],
            "language": "en",
            "category": "geopolitics",
        },
        auction_window_seconds=0.0,
        mock_bids=[BidRecord(agent_address="0xstream", bid_amount=1.0)],
    )

    await asyncio.wait_for(reader_task, timeout=5.0)

    types = {m.get("event") for m in seen}
    assert "hello" in types
    assert "polymarket.submitted" in types
    # The other key transitions should show up too.
    assert "event.created" in types
    assert "auction.settled" in types


def test_sse_route_registered(isolated_db: str) -> None:
    """Smoke test: the SSE route is wired into the FastAPI app."""

    app = _build_app()
    routes = {getattr(r, "path", None) for r in app.routes}
    assert "/sse/events" in routes
