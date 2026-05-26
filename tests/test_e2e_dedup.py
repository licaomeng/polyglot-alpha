"""E2E tests for the 5-minute sliding-window dedup and the RSS uuid salt.

Exercises the trigger route's dedup behaviour via httpx.AsyncClient +
ASGITransport (in-process FastAPI, no live server). MockLLM is forced
on by clearing ANTHROPIC_API_KEY.
"""

from __future__ import annotations

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
    """Force lifecycle to reach SUBMITTED quickly."""

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


async def _post_trigger(client: httpx.AsyncClient, title: str) -> dict[str, Any]:
    r = await client.post(
        "/trigger/event",
        json={
            "title": title,
            "sources": [{"name": "test", "url": "https://example.com/x"}],
            "auction_window_seconds": 0.0,
            "mock_bids": [
                {"agent_address": "0xdedup", "bid_amount": 1.0},
                {"agent_address": "0xdedup_b", "bid_amount": 3.0},
            ],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_5_min_sliding_dedup_returns_same_event_id(
    isolated_db: str,
) -> None:
    """Two posts with identical title within 5 min => same event_id, deduped=True."""

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await _post_trigger(client, "Sliding dedup test event")
        first_id = first["event_id"]
        assert first.get("deduped") is not True

        second = await _post_trigger(client, "Sliding dedup test event")
        assert second["event_id"] == first_id
        assert second["deduped"] is True


@pytest.mark.asyncio
async def test_5_min_sliding_dedup_expires_after_window(
    isolated_db: str,
) -> None:
    """Once an event ages past 5 min the next identical-title click is fresh.

    We don't actually wait 5 minutes — we backdate the persisted ``triggered_at``
    of the first event by 6 minutes so the sliding window query stops
    matching it. The orchestrator's permanent ``content_hash`` dedup is
    sidestepped by giving each event a different ``sources`` payload,
    matching how the trigger route degrades.
    """

    from polyglot_alpha.persistence.db import engine
    from polyglot_alpha.persistence.models import Event

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        title = "Expiring dedup test event"
        # First click — vanilla.
        first = await _post_trigger(client, title)
        first_id = first["event_id"]

        # Backdate the first event 6 minutes ago so the 5-min slider misses it.
        with Session(engine) as s:
            row = s.get(Event, first_id)
            assert row is not None
            row.triggered_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=6)
            # Also tweak the content_hash so the orchestrator's permanent
            # content-hash dedup doesn't short-circuit the second post —
            # this matches what a fresh RSS click with different sources
            # would produce in production.
            row.content_hash = row.content_hash + "_aged"
            s.add(row)
            s.commit()

        # Second click — sliding dedup miss => new event row.
        r = await client.post(
            "/trigger/event",
            json={
                "title": title,
                # Different sources => different content_hash; sidesteps the
                # permanent dedup that runs after the slider.
                "sources": [{"name": "test2", "url": "https://example.com/y"}],
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    {"agent_address": "0xexpired", "bid_amount": 1.0},
                    {"agent_address": "0xexpired_b", "bid_amount": 3.0},
                ],
            },
        )
        assert r.status_code == 200, r.text
        second = r.json()
        assert second["event_id"] != first_id, "expected fresh event_id after window"
        assert second.get("deduped") is not True


@pytest.mark.asyncio
async def test_uuid_salt_makes_each_rss_click_unique(
    isolated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three RSS placeholder clicks produce three distinct event_ids.

    The trigger route salts the RSS placeholder title with
    ``uuid.uuid4().hex`` so dedup never collapses repeat clicks. We
    monkey-patch the RSS-fetch helper so the BackgroundTask never runs
    (we don't need it for the dedup check) and just verify three POSTs
    produce three event_ids.
    """

    from polyglot_alpha.api.routes import trigger as trigger_mod

    async def _no_op_fetch(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(trigger_mod, "_fetch_rss_demo_event", _no_op_fetch)

    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    seen_ids: set[int] = set()
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(3):
            r = await client.post(
                "/trigger/event",
                json={
                    "event_source": "rss",
                    "auction_window_seconds": 0.0,
                    "mock_bids": [
                        {"agent_address": "0xrss_a", "bid_amount": 1.0},
                        {"agent_address": "0xrss_b", "bid_amount": 3.0},
                    ],
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body.get("deduped") is not True, "RSS placeholder must never dedup"
            event_id = body.get("event_id")
            assert isinstance(event_id, int)
            seen_ids.add(event_id)

    assert len(seen_ids) == 3, f"expected 3 distinct RSS event_ids, got {seen_ids}"
