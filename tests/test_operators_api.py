"""Integration tests for the /api/operators routes."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from polyglot_alpha.api.main import create_app


@pytest.fixture()
def client(isolated_db) -> TestClient:
    """Create a TestClient with an isolated SQLite DB."""

    app = create_app()
    return TestClient(app)


def _stub_chain_register(*_args, **_kwargs):
    return {"stake_tx": "0xabc123stake", "register_tx": "0xdef456register"}


def test_register_operator_creates_row_and_returns_seed_repuration(
    client: TestClient,
) -> None:
    with patch(
        "polyglot_alpha.api.routes.operators._try_register_on_chain",
        side_effect=_stub_chain_register,
    ):
        resp = client.post(
            "/api/operators/register",
            json={
                "operator_address": "0xtestop001",
                "display_name": "Test Operator",
                "signature": "0xdeadbeef",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "registered"
    assert body["stake_tx"] == "0xabc123stake"
    assert body["reputation_tx"] == "0xdef456register"
    assert body["initial_reputation"] == 0.7
    assert body["auction_stream_url"] == "/sse/auctions"
    assert body["operator_address"] == "0xtestop001"


def test_register_operator_idempotent_for_new_address(client: TestClient) -> None:
    """Re-registering an address with 0 bids/wins is allowed (only commits one row)."""

    with patch(
        "polyglot_alpha.api.routes.operators._try_register_on_chain",
        side_effect=_stub_chain_register,
    ):
        client.post(
            "/api/operators/register",
            json={
                "operator_address": "0xtestop002",
                "display_name": "Test 2",
                "signature": "0xab",
            },
        )
        # Second call with no bids -> not yet 409.
        resp = client.post(
            "/api/operators/register",
            json={
                "operator_address": "0xtestop002",
                "display_name": "Test 2",
                "signature": "0xab",
            },
        )
    assert resp.status_code == 200


def test_list_operators_returns_seeders_and_externals(client: TestClient) -> None:
    with patch(
        "polyglot_alpha.api.routes.operators._try_register_on_chain",
        side_effect=_stub_chain_register,
    ):
        client.post(
            "/api/operators/register",
            json={
                "operator_address": "0xtestop003",
                "display_name": "External",
                "signature": "0xab",
            },
        )

    resp = client.get("/api/operators")
    assert resp.status_code == 200
    operators = resp.json()
    assert isinstance(operators, list)
    # The external operator we just registered must appear with kind=external.
    matches = [o for o in operators if o["address"] == "0xtestop003"]
    assert len(matches) == 1
    assert matches[0]["kind"] == "external"


def test_get_operator_returns_arc_explorer_url(client: TestClient) -> None:
    with patch(
        "polyglot_alpha.api.routes.operators._try_register_on_chain",
        side_effect=_stub_chain_register,
    ):
        client.post(
            "/api/operators/register",
            json={
                "operator_address": "0xtestop004",
                "display_name": "Lookup Target",
                "signature": "0xab",
            },
        )

    resp = client.get("/api/operators/0xtestop004")
    assert resp.status_code == 200
    body = resp.json()
    assert "arc_explorer_url" in body
    assert "testnet.arc.network" in body["arc_explorer_url"]
    assert body["kind"] == "external"
    assert body["reputation"] == 0.7


def test_get_unknown_operator_returns_404(client: TestClient) -> None:
    resp = client.get("/api/operators/0xdoesnotexist1234567890abcdef12345678")
    assert resp.status_code == 404


def test_register_operator_mock_mode_returns_sim_tx_and_no_chain_call(
    client: TestClient,
) -> None:
    """Mock mode must skip the chain helper and return ``0xsim_`` tx hashes."""

    with patch(
        "polyglot_alpha.api.routes.operators._try_register_on_chain"
    ) as chain_mock:
        resp = client.post(
            "/api/operators/register",
            json={
                "operator_address": "0xtestop900",
                "display_name": "Mock Operator",
                "model_label": "claude-opus-4-7",
                "languages": ["zh", "ja"],
                "stake_amount_usdc": 100.0,
                "mode": "mock",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_simulated"] is True
    assert body["success"] is True
    assert body["stake_tx"].startswith("0xsim_")
    assert body["reputation_tx"].startswith("0xsim_")
    assert body["registration_id"].startswith("reg_")
    chain_mock.assert_not_called()


def test_register_operator_rejects_unknown_language(client: TestClient) -> None:
    resp = client.post(
        "/api/operators/register",
        json={
            "operator_address": "0xtestop901",
            "display_name": "Bad Langs",
            "languages": ["zh", "klingon"],
            "mode": "mock",
        },
    )
    assert resp.status_code == 422
    assert "unsupported_language_codes" in resp.text


def test_pending_fees_returns_cumulative_for_operator(
    client: TestClient, isolated_db
) -> None:
    """``/operators/{addr}/pending-fees`` mirrors AgentReputation.cumulative_fees."""

    from datetime import datetime, timezone

    from polyglot_alpha.persistence.db import session_scope
    from polyglot_alpha.persistence.models import AgentReputation

    addr = "0xtestop910"
    with session_scope() as s:
        s.add(
            AgentReputation(
                agent_address=addr,
                avg_quality=0.8,
                cumulative_fees=42.5,
                last_updated=datetime.now(timezone.utc),
            )
        )

    resp = client.get(f"/api/operators/{addr}/pending-fees")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operator_address"] == addr
    assert body["pending_usdc"] == 42.5
    assert body["event_count"] == 0


def test_claim_fees_mock_mode_zeros_balance_and_returns_sim_tx(
    client: TestClient, isolated_db
) -> None:
    from datetime import datetime, timezone

    from polyglot_alpha.persistence.db import session_scope
    from polyglot_alpha.persistence.models import AgentReputation

    addr = "0xtestop911"
    with session_scope() as s:
        s.add(
            AgentReputation(
                agent_address=addr,
                avg_quality=0.8,
                cumulative_fees=17.25,
                last_updated=datetime.now(timezone.utc),
            )
        )

    resp = client.post(
        f"/api/operators/{addr}/claim-fees",
        json={"mode": "mock"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["is_simulated"] is True
    assert body["amount_claimed_usdc"] == 17.25
    assert body["tx_hash"].startswith("0xsim_")

    # Subsequent claim should be a no-op.
    follow = client.post(
        f"/api/operators/{addr}/claim-fees",
        json={"mode": "mock"},
    )
    assert follow.status_code == 200
    assert follow.json()["success"] is False
    assert follow.json()["amount_claimed_usdc"] == 0.0


def test_claim_fees_returns_404_for_unknown_operator(client: TestClient) -> None:
    resp = client.post(
        "/api/operators/0xnotanoperator9999999999999999999999999/claim-fees",
        json={"mode": "mock"},
    )
    assert resp.status_code == 404


def test_relay_bid_endpoint_is_phase_2_stub(client: TestClient) -> None:
    resp = client.post(
        "/api/auctions/evt-42/bid",
        json={
            "operator_address": "0xtestop005",
            "bid_amount_usdc": 1.25,
            "candidate_hash": "0x" + "ab" * 32,
            "signature": "0xsig",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "phase_2_stub"
    assert body["tx_hash"] is None
