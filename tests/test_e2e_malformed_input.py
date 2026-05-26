"""E2E tests for malformed /trigger/event payloads.

Verifies the Pydantic validators in
``polyglot_alpha.api.routes.trigger`` reject ill-formed inputs with a
422 before they reach the orchestrator. Exercises:

* empty title in ``user_payload`` mode
* invalid language code (length cap + non-empty when relevant)
* negative bid amounts
* mock_bids list larger than MAX_BIDS_PER_REQUEST
* NaN / inf bid amounts

Uses TestClient (synchronous) — these tests do not run a real lifecycle
so MockLLM and judge stubs are not needed.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _build_app() -> Any:
    from polyglot_alpha.api.main import create_app

    return create_app()


def _baseline_bids() -> list[dict[str, Any]]:
    return [{"agent_address": "0xagent", "bid_amount": 1.0}]


# ---------------------------------------------------------------------------
# 1. Empty title in ``user_payload`` mode -> 422.
# ---------------------------------------------------------------------------


def test_trigger_with_empty_title_user_payload_422(isolated_db: str) -> None:
    """``title=""`` (or whitespace) must be rejected with 422.

    The route enforces this explicitly — Pydantic's ``max_length`` allows
    empty strings, but the trigger handler raises HTTPException(422) when
    title is empty for ``user_payload``.
    """

    app = _build_app()
    with TestClient(app) as client:
        # Whitespace-only title -> 422.
        r = client.post(
            "/trigger/event",
            json={
                "title": "   ",
                "sources": [{"name": "t", "url": "https://example.com"}],
                "auction_window_seconds": 0.0,
                "mock_bids": _baseline_bids(),
            },
        )
        assert r.status_code == 422, r.text

        # Empty title -> 422.
        r2 = client.post(
            "/trigger/event",
            json={
                "title": "",
                "sources": [{"name": "t", "url": "https://example.com"}],
                "auction_window_seconds": 0.0,
                "mock_bids": _baseline_bids(),
            },
        )
        assert r2.status_code == 422, r2.text


# ---------------------------------------------------------------------------
# 2. Invalid language code -> 422.
# ---------------------------------------------------------------------------


def test_trigger_with_invalid_language_code_422(isolated_db: str) -> None:
    """A 64-char language string overflows the 16-char Pydantic cap -> 422.

    The TriggerRequest model declares ``language: str = Field(default="en",
    max_length=16)``. A 64-char garbage string therefore fails validation.
    A 3-char string like ``"xxx"`` is accepted (length OK) and the
    orchestrator falls back to that as an opaque tag, which is the
    documented behaviour.
    """

    app = _build_app()
    with TestClient(app) as client:
        # Overlong language code -> 422 (length cap).
        r = client.post(
            "/trigger/event",
            json={
                "title": "Language overflow test",
                "language": "x" * 64,
                "sources": [{"name": "t", "url": "https://example.com"}],
                "auction_window_seconds": 0.0,
                "mock_bids": _baseline_bids(),
            },
        )
        assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 3. Negative bid amount -> 422.
# ---------------------------------------------------------------------------


def test_trigger_with_negative_bid_amount_422(isolated_db: str) -> None:
    """``bid_amount=-1`` violates ``ge=MIN_BID_AMOUNT`` -> 422."""

    app = _build_app()
    with TestClient(app) as client:
        r = client.post(
            "/trigger/event",
            json={
                "title": "Negative bid test",
                "sources": [{"name": "t", "url": "https://example.com"}],
                "auction_window_seconds": 0.0,
                "mock_bids": [
                    {"agent_address": "0xneg", "bid_amount": -1.0},
                ],
            },
        )
        assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 4. mock_bids list overflow -> 422.
# ---------------------------------------------------------------------------


def test_trigger_with_too_many_bids_rejects(isolated_db: str) -> None:
    """21 mock_bids > MAX_BIDS_PER_REQUEST (20) -> 422."""

    app = _build_app()
    with TestClient(app) as client:
        bids = [
            {"agent_address": f"0xbid{i:02d}", "bid_amount": float(i + 1)}
            for i in range(21)
        ]
        r = client.post(
            "/trigger/event",
            json={
                "title": "Too many bids test",
                "sources": [{"name": "t", "url": "https://example.com"}],
                "auction_window_seconds": 0.0,
                "mock_bids": bids,
            },
        )
        assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 5. NaN / inf bid amount -> 422.
# ---------------------------------------------------------------------------


def test_trigger_with_nan_inf_in_bid_rejected(isolated_db: str) -> None:
    """``bid_amount=inf`` / ``nan`` rejected by the explicit finite validator.

    The TriggerBid model has a ``_reject_non_finite`` validator. We
    invoke the Pydantic model directly to verify it raises, because
    sending NaN/Inf through HTTP triggers a downstream JSON-encoding
    crash in FastAPI's 422 path (the rejected float gets echoed into
    ``detail.input``, where the JSON encoder cannot serialise NaN/Inf —
    documented in E3_test_findings.md).
    """

    from pydantic import ValidationError

    from polyglot_alpha.api.routes.trigger import TriggerBid

    # NaN, +inf, -inf are all rejected by the chain of validators
    # (Pydantic ``le``/``ge`` bounds + the explicit ``_reject_non_finite``
    # validator). Any one of them triggering ValidationError satisfies
    # the "never reaches the orchestrator" contract.
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            TriggerBid(agent_address="0xnonfinite", bid_amount=bad)
