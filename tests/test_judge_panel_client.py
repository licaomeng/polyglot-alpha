"""Offline unit tests for :mod:`polyglot_alpha.chain.judge_panel_client` (W9-A).

These tests are deliberately decoupled from any real RPC: we stub the
``OnChainClient`` constructor and replace ``contract.functions.*`` /
``w3.eth.*`` with :class:`unittest.mock.MagicMock` instances. Each test
asserts that the JudgePanel adapter builds the right TX (correct contract
function called with correct args) without ever touching the network.

Run with: ``.venv/bin/pytest tests/test_judge_panel_client.py -q``
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from polyglot_alpha.chain import judge_panel_client as jpc_mod
from polyglot_alpha.chain.judge_panel_client import (
    JudgePanelClient,
    SCORE_SCALE,
    attestation_hash_for_dossier,
    canonical_dossier_json,
    record_aggregate_attestation,
    scale_overall_score,
)
from polyglot_alpha.chain.sim_helpers import (
    SIM_TX_HASH_PREFIX,
    is_sim_hash,
    set_event_mode,
    reset_event_mode,
)
from polyglot_alpha.onchain import OnChainClient


_OPERATOR_PK = (
    "0x4c0883a69102937d6231471b5dbb6204fe512961708279e7b1d3b3e4b8c5a0e1"
)


@pytest.fixture(autouse=True)
def _operator_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide deterministic env vars for every chain-client test."""

    monkeypatch.setenv("HACKATHON_WALLET_PRIVATE_KEY", _OPERATOR_PK)
    monkeypatch.setenv(
        "JUDGE_PANEL_ADDRESS",
        "0x1eE7BADc48b52B36e086adb4a98E00cbff4efd9a",
    )
    # Make sure no leftover mock-mode contextvar from another test bleeds
    # into the live-path assertions below.
    token = set_event_mode(None)
    yield
    reset_event_mode(token)


def _build_mock_onchain() -> MagicMock:
    """Return a MagicMock that quacks like ``OnChainClient``."""

    mock = MagicMock(spec=OnChainClient)
    mock.w3 = MagicMock()
    mock.w3.eth = MagicMock()
    mock.auction = MagicMock()
    mock.reputation = MagicMock()
    mock.usdc = MagicMock()
    mock.w3.eth.contract = MagicMock(return_value=MagicMock())
    mock._build_base_txn = MagicMock(return_value={"nonce": 1, "chainId": 31337})
    mock._send = MagicMock(return_value="abcd" * 16)
    return mock


# --------------------------------------------------------------------------- #
# Pure helpers                                                                #
# --------------------------------------------------------------------------- #


def test_canonical_dossier_json_is_deterministic() -> None:
    """Two equivalent dossiers (different key order) hash identically."""

    a = [{"name": "BLEU", "score": 0.87, "passed": True}]
    b = [{"passed": True, "score": 0.87, "name": "BLEU"}]
    assert canonical_dossier_json(a) == canonical_dossier_json(b)
    assert attestation_hash_for_dossier(a) == attestation_hash_for_dossier(b)


def test_attestation_hash_is_32_bytes() -> None:
    """Hash output must be EVM ``bytes32`` (exactly 32 bytes)."""

    dossier = [{"name": "MQM", "score": 78}]
    assert len(attestation_hash_for_dossier(dossier)) == 32


def test_attestation_hash_changes_when_dossier_changes() -> None:
    """Tampering with any field flips the hash."""

    a = [{"name": "BLEU", "score": 0.87, "passed": True}]
    b = [{"name": "BLEU", "score": 0.88, "passed": True}]  # 0.87 -> 0.88
    assert attestation_hash_for_dossier(a) != attestation_hash_for_dossier(b)


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.0, 0),
        (0.876, 876),
        (1.0, SCORE_SCALE),
        (-0.1, 0),  # clamped
        (78.5, 785),  # 0-100 scale → 785/1000
    ],
)
def test_scale_overall_score(score: float, expected: int) -> None:
    assert scale_overall_score(score) == expected


# --------------------------------------------------------------------------- #
# JudgePanelClient construction + reads                                       #
# --------------------------------------------------------------------------- #


def test_judge_panel_client_constructs_with_mock_onchain() -> None:
    mock = _build_mock_onchain()
    client = JudgePanelClient(onchain=mock)
    mock.w3.eth.contract.assert_called_once()
    assert client.contract is not None


def test_judge_panel_client_is_registered_judge_reads_get_judge_info() -> None:
    mock = _build_mock_onchain()
    fake_contract = MagicMock()
    fake_contract.functions.getJudgeInfo.return_value.call.return_value = (
        2_000_000,
        True,
        False,
        7,
    )
    mock.w3.eth.contract = MagicMock(return_value=fake_contract)
    client = JudgePanelClient(onchain=mock)

    is_registered = asyncio.run(
        client.is_registered_judge("0x" + "ab" * 20)
    )

    assert is_registered is True
    fake_contract.functions.getJudgeInfo.assert_called_once()


# --------------------------------------------------------------------------- #
# record_attestation                                                          #
# --------------------------------------------------------------------------- #


def test_record_attestation_builds_correct_tx_args() -> None:
    """``recordAttestation`` is called with bytes32, address, uint, bytes32."""

    mock = _build_mock_onchain()
    fake_contract = MagicMock()
    fake_contract.functions.recordAttestation.return_value.build_transaction.return_value = {
        "to": "0xdead",
        "data": "0x",
        "gas": 120_000,
    }
    mock.w3.eth.contract = MagicMock(return_value=fake_contract)
    client = JudgePanelClient(onchain=mock)

    dossier = [
        {"name": "BLEU", "score": 0.87, "passed": True, "reason": ""},
        {"name": "MQM", "score": 78, "passed": True, "reason": ""},
    ]
    hash_bytes = attestation_hash_for_dossier(dossier)

    judge_addr = "0x" + "ab" * 20
    tx_hash = asyncio.run(
        client.record_attestation(
            event_id="event-9", judge=judge_addr, score_scaled=876,
            attestation_hash=hash_bytes,
        )
    )

    assert tx_hash.startswith("0x")
    assert len(tx_hash) == 66  # 0x + 64 hex chars
    fake_contract.functions.recordAttestation.assert_called_once()
    args = fake_contract.functions.recordAttestation.call_args.args
    # eventId bytes32, judge address, score uint256, attestationHash bytes32
    assert len(args[0]) == 32
    assert args[1].lower() == judge_addr  # checksummed = lowercase for repeated bytes
    assert args[2] == 876
    assert args[3] == hash_bytes


def test_record_attestation_rejects_non_bytes32_hash() -> None:
    """A hash that's not exactly 32 bytes is rejected before any chain call."""

    mock = _build_mock_onchain()
    client = JudgePanelClient(onchain=mock)
    with pytest.raises(ValueError):
        asyncio.run(
            client.record_attestation(
                event_id="ev",
                judge="0x" + "ab" * 20,
                score_scaled=100,
                attestation_hash=b"\x00" * 10,  # wrong length
            )
        )


def test_record_attestation_mock_mode_returns_sim_hash() -> None:
    """In mock mode, ``recordAttestation`` returns ``0xsim_*`` without RPC."""

    mock = _build_mock_onchain()
    client = JudgePanelClient(onchain=mock)
    token = set_event_mode("mock")
    try:
        tx_hash = asyncio.run(
            client.record_attestation(
                event_id="ev",
                judge="0x" + "ab" * 20,
                score_scaled=900,
                attestation_hash=b"\x11" * 32,
            )
        )
    finally:
        reset_event_mode(token)
    assert is_sim_hash(tx_hash)
    assert tx_hash.startswith(SIM_TX_HASH_PREFIX)
    # No actual contract call should have been built.
    # (recordAttestation function may have been accessed via attribute
    # lookup but build_transaction must NOT have been invoked.)


# --------------------------------------------------------------------------- #
# record_aggregate_attestation (orchestrator entry-point)                     #
# --------------------------------------------------------------------------- #


def test_record_aggregate_attestation_mock_mode_returns_sim_payload() -> None:
    """Mock-mode aggregate returns a fully-shaped dict with sim tx_hash."""

    token = set_event_mode("mock")
    try:
        result = asyncio.run(
            record_aggregate_attestation(
                event_id="evt-mock",
                overall_score=0.91,
                judges_dossier=[
                    {"name": "BLEU", "passed": True, "score": 0.91, "reason": ""},
                ],
            )
        )
    finally:
        reset_event_mode(token)
    assert is_sim_hash(result["tx_hash"])
    assert result["attestation_hash"].startswith("0x")
    assert len(result["attestation_hash"]) == 66
    assert result["score_scaled"] == 910
    assert result["strategy"] == "gamma_aggregate"
    assert result["register_tx"] is None


def test_record_aggregate_attestation_live_calls_recordAttestation() -> None:
    """Live mode invokes recordAttestation once and never registers when
    the aggregator is already on-chain."""

    mock = _build_mock_onchain()
    fake_contract = MagicMock()
    fake_contract.functions.recordAttestation.return_value.build_transaction.return_value = {
        "to": "0xdead", "data": "0x", "gas": 120_000,
    }
    # getJudgeInfo says "yes, this wallet is a translation judge" so we
    # skip the register leg.
    fake_contract.functions.getJudgeInfo.return_value.call.return_value = (
        2_000_000, True, False, 0,
    )
    mock.w3.eth.contract = MagicMock(return_value=fake_contract)
    client = JudgePanelClient(onchain=mock)

    result = asyncio.run(
        record_aggregate_attestation(
            event_id="evt-live",
            overall_score=0.74,
            judges_dossier=[{"name": "MQM", "passed": True, "score": 74, "reason": ""}],
            client=client,
        )
    )

    assert result["tx_hash"].startswith("0x")
    assert result["score_scaled"] == 740
    assert result["register_tx"] is None  # already registered
    fake_contract.functions.recordAttestation.assert_called_once()
