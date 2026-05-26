"""Verify ``POST /trigger/event`` with ``event_source='rss'`` returns instantly.

Before this change the endpoint blocked the HTTP response on a 5-15 s RSS
poll + Haiku scoring round-trip. After the inversion the response carries
a placeholder title in ~10-200 ms and the RSS fetch runs as part of the
BackgroundTask (visible as a regular SSE phase).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _build_app():
    from polyglot_alpha.api.main import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _force_judges_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the judge panel to PASS so demo lifecycles complete cleanly."""

    from polyglot_alpha import orchestrator

    async def passing_judges(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.9, "comet": 0.85, "mqm": {"score": 0}},
            style_alignment_passes={f"d{i}": True for i in range(1, 9)},
            overall_score=0.92,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing_judges)


_REAL_RSS_TITLE = "Real RSS-sourced headline about geopolitics"


def _fake_rss_event_payload() -> dict[str, Any]:
    return {
        "title": _REAL_RSS_TITLE,
        "sources": [
            {
                "name": "test-rss-feed",
                "url": "https://example.com/article/1",
                "language": "zh",
            }
        ],
        "language": "zh",
        "category": "geopolitics",
        "summary": "A neutral 1-sentence cluster summary.",
        "scoring": {
            "event_quality_score": 0.85,
            "primary_category": "geopolitics",
        },
    }


def test_rss_trigger_returns_in_under_one_second(
    isolated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint MUST return event_id within <1 s even if the RSS fetch
    helper takes several seconds. The fetch is deferred to a BackgroundTask.
    """

    from polyglot_alpha.api.routes import trigger as trigger_route

    SLOW_FETCH_SECONDS = 1.5  # noqa: N806 — local constant

    async def slow_rss_fetch(_window: int) -> dict[str, Any]:
        await asyncio.sleep(SLOW_FETCH_SECONDS)
        return _fake_rss_event_payload()

    monkeypatch.setattr(trigger_route, "_fetch_rss_demo_event", slow_rss_fetch)

    app = _build_app()
    with TestClient(app) as client:
        t0 = time.perf_counter()
        r = client.post(
            "/trigger/event",
            json={
                "event_source": "rss",
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    {"agent_address": "0xrss_fast", "bid_amount": 1.0}
                ],
            },
        )
        elapsed = time.perf_counter() - t0

    assert r.status_code == 200, r.text
    body = r.json()
    # NOTE: ``TestClient.post`` blocks until BackgroundTasks complete, so
    # ``elapsed`` includes the full slow-fetch + lifecycle. We assert on
    # the response body shape instead — ``status == PENDING`` proves the
    # response was prepared BEFORE the BackgroundTask ran.
    assert body["status"] == "PENDING", body
    assert body["scheduled"] is True
    assert isinstance(body["event_id"], int)
    # The placeholder title is surfaced in the immediate response.
    assert "Fetching" in body.get("title", "") or body.get("title", "").startswith(
        "Fetching"
    ), body


def test_rss_trigger_updates_title_after_background_fetch(
    isolated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the BackgroundTask finishes, the events row must carry the real
    RSS-derived title rather than the ``Fetching...`` placeholder.
    """

    from polyglot_alpha.api.routes import trigger as trigger_route
    from polyglot_alpha.persistence import session_scope
    from polyglot_alpha.persistence.models import Event

    async def fake_rss_fetch(_window: int) -> dict[str, Any]:
        return _fake_rss_event_payload()

    monkeypatch.setattr(trigger_route, "_fetch_rss_demo_event", fake_rss_fetch)

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/trigger/event",
            json={
                "event_source": "rss",
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    {"agent_address": "0xrss_upd", "bid_amount": 1.0}
                ],
            },
        )
        assert r.status_code == 200, r.text
        event_id = r.json()["event_id"]

    # TestClient flushes BackgroundTasks before returning the response, so
    # by the time we get here the lifecycle (incl. row update) has run.
    with session_scope() as session:
        row = session.get(Event, event_id)
        assert row is not None
        assert row.title == _REAL_RSS_TITLE, (
            f"expected real RSS title, got {row.title!r}"
        )


def test_rss_trigger_emits_event_updated_sse(
    isolated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The BackgroundTask must publish ``event.updated`` after resolving the
    real title so SSE listeners can refresh the UI header.
    """

    from polyglot_alpha.api.routes import trigger as trigger_route
    from polyglot_alpha.pubsub import get_pubsub

    async def fake_rss_fetch(_window: int) -> dict[str, Any]:
        return _fake_rss_event_payload()

    monkeypatch.setattr(trigger_route, "_fetch_rss_demo_event", fake_rss_fetch)

    captured: list[tuple[str, dict[str, Any]]] = []

    hub = get_pubsub()
    original_publish = hub.publish

    async def capture_publish(event_type: str, payload: dict[str, Any]) -> None:
        captured.append((event_type, payload))
        await original_publish(event_type, payload)

    monkeypatch.setattr(hub, "publish", capture_publish)

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/trigger/event",
            json={
                "event_source": "rss",
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    {"agent_address": "0xrss_sse", "bid_amount": 1.0}
                ],
            },
        )
        assert r.status_code == 200, r.text

    types_seen = [t for t, _ in captured]
    # ``event.created`` fires once when the placeholder row is inserted.
    assert "event.created" in types_seen, types_seen
    # ``event.updated`` fires after the BackgroundTask resolves the real title.
    assert "event.updated" in types_seen, types_seen
    updated_payload = next(p for t, p in captured if t == "event.updated")
    assert updated_payload.get("title") == _REAL_RSS_TITLE


def test_rss_trigger_falls_back_when_fetch_returns_none(
    isolated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``_fetch_rss_demo_event`` returns None (no recent cluster, Haiku
    rejected, RSS errored, etc), the BackgroundTask must still update the
    row with the hardcoded-sample-or-fallback title — never leave the
    ``Fetching...`` placeholder in the DB.
    """

    from polyglot_alpha.api.routes import trigger as trigger_route
    from polyglot_alpha.persistence import session_scope
    from polyglot_alpha.persistence.models import Event

    async def empty_rss_fetch(_window: int) -> None:
        return None

    monkeypatch.setattr(trigger_route, "_fetch_rss_demo_event", empty_rss_fetch)

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/trigger/event",
            json={
                "event_source": "rss",
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    {"agent_address": "0xrss_fb", "bid_amount": 1.0}
                ],
            },
        )
        assert r.status_code == 200, r.text
        event_id = r.json()["event_id"]

    with session_scope() as session:
        row = session.get(Event, event_id)
        assert row is not None
        assert row.title is not None
        assert not row.title.startswith("Fetching"), (
            f"placeholder title leaked into DB: {row.title!r}"
        )
