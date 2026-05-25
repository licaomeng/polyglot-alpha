"""Tests for ``/events/{id}/polymarket/submit-real``.

The endpoint promotes a dry-run market submission to a real Polymarket
POST. It enforces three operator safety gates before calling the live
client and exposes 400 errors when any gate fails:

  * ``confirm_real_submission`` must be ``True``.
  * The event's ``overall_score`` must clear ``REAL_QUALITY_GATE``.
  * A daily cap (``MAX_REAL_SUBMISSIONS_PER_DAY``) limits how many real
    submissions can land per 24h window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select


def _build_app():
    from polyglot_alpha.api.main import create_app

    return create_app()


def _trigger_event(client: TestClient, title: str = "real-submit demo") -> int:
    """Run a lifecycle and return the resulting event_id."""

    r = client.post(
        "/trigger/event",
        json={
            "title": title,
            "sources": [{"name": "src", "url": "https://example.com/a"}],
            "auction_window_seconds": 0.0,
            "mock_bids": [{"agent_address": "0xagent", "bid_amount": 1.0}],
        },
    )
    assert r.status_code == 200, r.text
    return int(r.json()["event_id"])


@pytest.fixture(autouse=True)
def _stub_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force judges PASS at 0.92 and stub the real submission path."""

    from polyglot_alpha import orchestrator

    async def passing_judges(_q: dict[str, Any]) -> orchestrator.JudgePanelResult:
        return orchestrator.JudgePanelResult(
            translation_scores={"bleu": 0.9},
            style_alignment_passes={f"d{i}": True for i in range(1, 4)},
            overall_score=0.92,
            verdict="PASS",
        )

    monkeypatch.setattr(orchestrator, "_evaluate_with_judges", passing_judges)
    monkeypatch.setenv("POLYMARKET_BUILDER_API_KEY", "test-key")
    monkeypatch.setenv("POLYMARKET_BUILDER_API_SECRET", "test-secret")
    monkeypatch.setenv("POLYMARKET_BUILDER_API_PASSPHRASE", "test-pass")


def _stub_real_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``PolymarketV2Client.submit_question`` with a fake real success.

    We do not patch the HTTP layer because the real-mode code path also
    validates env vars before hitting it; patching at the client method
    keeps the test focused on the endpoint's gating logic.
    """

    from polyglot_alpha.polymarket import client as polymarket_client
    from polyglot_alpha.polymarket.types import (
        PolymarketMode,
        SubmissionResult,
    )

    async def fake_submit_question(
        self, question, *, confirm_real_submission: bool = False,
        overall_score: float | None = None,
    ) -> SubmissionResult:
        return SubmissionResult(
            market_id="real-market-abc123",
            polymarket_url="https://polymarket.com/market/real-market-abc123",
            status="submitted",
            fees_estimate_usdc=2.0,
            is_simulated=False,
            mode=PolymarketMode.REAL.value,
            payload={"question": getattr(question, "text", "")},
        )

    monkeypatch.setattr(
        polymarket_client.PolymarketV2Client,
        "submit_question",
        fake_submit_question,
    )


def test_submit_real_endpoint_is_registered(isolated_db: str) -> None:
    """Smoke test: the route is wired into the FastAPI app."""

    app = _build_app()
    routes = {getattr(r, "path", None) for r in app.routes}
    assert "/events/{event_id}/polymarket/submit-real" in routes


def test_submit_real_rejects_missing_confirm_flag(isolated_db: str) -> None:
    """Empty / falsy body must return 400."""

    app = _build_app()
    with TestClient(app) as client:
        event_id = _trigger_event(client)
        r = client.post(
            f"/events/{event_id}/polymarket/submit-real", json={}
        )
        assert r.status_code == 400
        assert "confirm_real_submission" in r.text


def test_submit_real_rejects_unknown_event(isolated_db: str) -> None:
    """404 when the event id has never been triggered."""

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/events/999999/polymarket/submit-real",
            json={"confirm_real_submission": True},
        )
        assert r.status_code == 404


def test_submit_real_rejects_low_quality(
    isolated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Override the quality gate above the event's score -> 400."""

    app = _build_app()
    with TestClient(app) as client:
        event_id = _trigger_event(client)
        # Force the gate above the persisted score (0.92).
        from polyglot_alpha.api.routes import polymarket as poly_route

        monkeypatch.setattr(poly_route, "REAL_QUALITY_GATE", 0.99)
        r = client.post(
            f"/events/{event_id}/polymarket/submit-real",
            json={"confirm_real_submission": True},
        )
        assert r.status_code == 400
        assert "REAL_QUALITY_GATE" in r.text


def test_submit_real_happy_path(
    isolated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirmed + above-gate request reaches the (stubbed) real path."""

    _stub_real_submission(monkeypatch)

    app = _build_app()
    with TestClient(app) as client:
        event_id = _trigger_event(client)
        r = client.post(
            f"/events/{event_id}/polymarket/submit-real",
            json={"confirm_real_submission": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["submission"]["market_id"] == "real-market-abc123"
        assert body["submission"]["is_simulated"] is False
        assert body["mode"] == "real"

        # The DB row must reflect the new market_id + is_simulated=False.
        from polyglot_alpha.persistence.db import engine
        from polyglot_alpha.persistence.models import PolymarketSubmission

        with Session(engine) as s:
            row = s.exec(
                select(PolymarketSubmission)
                .where(PolymarketSubmission.event_id == event_id)
                .order_by(PolymarketSubmission.id.desc())
            ).first()
            assert row is not None
            assert row.market_id == "real-market-abc123"
            assert row.is_simulated is False


def test_submit_real_enforces_daily_cap(
    isolated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-seed 5 real submissions in the last 24h -> 6th request 400s."""

    _stub_real_submission(monkeypatch)

    app = _build_app()
    with TestClient(app) as client:
        event_id = _trigger_event(client)

        from polyglot_alpha.persistence.db import engine
        from polyglot_alpha.persistence.models import PolymarketSubmission

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=5
        )
        with Session(engine) as s:
            for i in range(5):
                s.add(
                    PolymarketSubmission(
                        event_id=event_id,
                        market_id=f"real-seeded-{i}",
                        status="SUBMITTED",
                        is_simulated=False,
                        submitted_at=now_naive,
                    )
                )
            s.commit()

        r = client.post(
            f"/events/{event_id}/polymarket/submit-real",
            json={"confirm_real_submission": True},
        )
        assert r.status_code == 400
        assert "daily" in r.text.lower()
