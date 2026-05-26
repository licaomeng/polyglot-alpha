"""Integration tests for the /api/operators/{addr}/withdraw-stake route.

Covers the three failure modes the route must distinguish:

  1. Mock-mode success — synthetic 0xsim_ tx + ledger reset.
  2. No-stake (404) — operator exists but has no withdrawable balance.
  3. Locked (409) — slashable window still open; ``locked_until_block``
     surfaced in the response body for the UI tooltip.

These tests do NOT hit Arc RPC; they monkeypatch the chain hooks
(``_try_withdraw_stake_on_chain``, ``_try_get_stake_status_on_chain``) and
the in-process mock stake ledger (``_MOCK_STAKE_LEDGER``).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from polyglot_alpha.api.main import create_app
from polyglot_alpha.api.routes import operators as operators_route


@pytest.fixture()
def client(isolated_db) -> TestClient:
    """Create a TestClient with an isolated SQLite DB."""

    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_mock_ledger():
    """Wipe the process-global mock stake ledger between tests."""

    operators_route._MOCK_STAKE_LEDGER.clear()
    yield
    operators_route._MOCK_STAKE_LEDGER.clear()


def _seed_operator_row(address: str) -> None:
    """Insert a minimal AgentReputation row so the operator passes the
    ``operator_not_found`` guard at the top of the route handler."""

    from polyglot_alpha.persistence.db import session_scope
    from polyglot_alpha.persistence.models import AgentReputation

    with session_scope() as s:
        s.add(
            AgentReputation(
                agent_address=address,
                avg_quality=0.8,
                last_updated=datetime.now(timezone.utc),
            )
        )


def test_withdraw_stake_mock_mode_returns_sim_tx_and_resets_ledger(
    client: TestClient,
) -> None:
    """Mock mode: returns 0xsim_ hash, recovers the seeded 5 USDC stake,
    leaves the ledger entry at amount_usdc=0 so a follow-up returns 404."""

    addr = "0xtestwsop001"
    _seed_operator_row(addr)
    operators_route._MOCK_STAKE_LEDGER[addr.lower()] = {
        "amount_usdc": operators_route.DEFAULT_AGENT_STAKE_USDC,
        "locked_until_block": None,
    }

    resp = client.post(
        f"/api/operators/{addr}/withdraw-stake",
        json={"mode": "mock"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["is_simulated"] is True
    assert body["amount_recovered_usdc"] == pytest.approx(
        operators_route.DEFAULT_AGENT_STAKE_USDC
    )
    assert body["tx_hash"].startswith("0xsim_")
    assert body["operator_address"] == addr

    # Ledger entry should now be drained.
    entry = operators_route._MOCK_STAKE_LEDGER[addr.lower()]
    assert entry["amount_usdc"] == 0.0
    assert entry["locked_until_block"] is None


def test_withdraw_stake_returns_404_when_no_stake(client: TestClient) -> None:
    """A registered operator with a drained mock ledger returns 404
    ``no_stake_to_withdraw`` so the UI can render "No active stake"."""

    addr = "0xtestwsop002"
    _seed_operator_row(addr)
    operators_route._MOCK_STAKE_LEDGER[addr.lower()] = {
        "amount_usdc": 0.0,
        "locked_until_block": None,
    }

    resp = client.post(
        f"/api/operators/{addr}/withdraw-stake",
        json={"mode": "mock"},
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["detail"] == "no_stake_to_withdraw"


def test_withdraw_stake_returns_409_when_stake_locked(
    client: TestClient,
) -> None:
    """When the slashable window is open the route must return 409 with
    ``locked_until_block`` in the response body so the UI can drive the
    tooltip text without re-reading the contract."""

    addr = "0xtestwsop003"
    locked_block = 12345678
    _seed_operator_row(addr)
    operators_route._MOCK_STAKE_LEDGER[addr.lower()] = {
        "amount_usdc": operators_route.DEFAULT_AGENT_STAKE_USDC,
        "locked_until_block": locked_block,
    }

    resp = client.post(
        f"/api/operators/{addr}/withdraw-stake",
        json={"mode": "mock"},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    detail = body["detail"]
    assert isinstance(detail, dict)
    assert detail["error"] == "stake_locked"
    assert detail["locked_until_block"] == locked_block
    assert "locked" in detail["message"].lower()


def test_withdraw_stake_returns_404_for_unknown_operator(
    client: TestClient,
) -> None:
    """Sanity guard: addresses that are neither a seeder nor a registered
    operator hit the standard ``operator_not_found`` 404."""

    resp = client.post(
        "/api/operators/0xunknownoperator999999999999999999999999/withdraw-stake",
        json={"mode": "mock"},
    )
    assert resp.status_code == 404


def test_stake_status_endpoint_reflects_mock_ledger(client: TestClient) -> None:
    """``GET /stake-status`` should mirror the mock ledger so the UI can
    pre-flight the button's enabled/disabled state."""

    addr = "0xtestwsop004"
    _seed_operator_row(addr)
    operators_route._MOCK_STAKE_LEDGER[addr.lower()] = {
        "amount_usdc": operators_route.DEFAULT_AGENT_STAKE_USDC,
        "locked_until_block": None,
    }

    resp = client.get(f"/api/operators/{addr}/stake-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["staked"] is True
    assert body["amount_usdc"] == pytest.approx(
        operators_route.DEFAULT_AGENT_STAKE_USDC
    )
    assert body["locked_until_block"] is None
    assert body["can_withdraw"] is True
