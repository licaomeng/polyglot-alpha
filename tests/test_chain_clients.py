"""Offline unit tests for :mod:`polyglot_alpha.chain`.

These tests are deliberately decoupled from any real RPC: we stub the
``OnChainClient`` constructor and replace ``contract.functions.*`` /
``w3.eth.*`` with :class:`unittest.mock.MagicMock` instances. Each test
asserts that the chain client builds the right TX (correct contract
function called with correct args) without ever touching the network.

Run with: ``.venv/bin/pytest tests/test_chain_clients.py -q``
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account

from polyglot_alpha.chain import (
    AuctionClient,
    BuilderFeeRouter,
    QuestionRegistry,
    ReputationRegistryClient,
)
from polyglot_alpha.chain import (
    auction_client as auction_mod,
    builder_fee_router as builder_mod,
    question_registry as qr_mod,
    reputation_registry as rep_mod,
)
from polyglot_alpha.onchain import OnChainClient, event_id_to_bytes32


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FOUNDRY_OUT = _REPO_ROOT / "contracts" / "out"

_CONTRACT_NAMES = (
    "TranslationAuction",
    "QuestionRegistry",
    "BuilderFeeRouter",
    "ReputationRegistry",
)

_OPERATOR_PK = (
    "0x4c0883a69102937d6231471b5dbb6204fe512961708279e7b1d3b3e4b8c5a0e1"
)
_AGENT_PK = (
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
)


@pytest.fixture(autouse=True)
def _operator_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide deterministic env vars for every chain-client test."""

    monkeypatch.setenv("HACKATHON_WALLET_PRIVATE_KEY", _OPERATOR_PK)
    monkeypatch.setenv(
        "TRANSLATION_AUCTION_ADDRESS",
        "0xE046Ea8478855A653bAdc9Fbd12ae4B8A429907a",
    )
    monkeypatch.setenv(
        "QUESTION_REGISTRY_ADDRESS",
        "0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1",
    )
    monkeypatch.setenv(
        "BUILDER_FEE_ROUTER_ADDRESS",
        "0xcE7596d9b21333Eae441E912699514F6fBD150e5",
    )
    monkeypatch.setenv(
        "REPUTATION_REGISTRY_ADDRESS",
        "0x00267FD2FFabDDB48bBF16e3a91C15DE260eF9F1",
    )


def _build_mock_onchain() -> MagicMock:
    """Return a MagicMock that quacks like ``OnChainClient`` for our purposes."""

    mock = MagicMock(spec=OnChainClient)
    mock.w3 = MagicMock()
    mock.w3.eth = MagicMock()
    # The auction/reputation/usdc contract attributes are MagicMocks whose
    # ``functions.<name>(...)`` chain returns yet another MagicMock that
    # exposes ``.build_transaction`` returning a dict.
    mock.auction = MagicMock()
    mock.reputation = MagicMock()
    mock.usdc = MagicMock()
    # When the clients build their own contract via ``w3.eth.contract``,
    # route that to a fresh MagicMock so each call gets a clean handle.
    mock.w3.eth.contract = MagicMock(return_value=MagicMock())
    # ``_build_base_txn`` must return a dict so ``{**base, "gas": ...}``
    # doesn't blow up.
    mock._build_base_txn = MagicMock(return_value={"nonce": 1, "chainId": 31337})
    # ``_send`` returns a tx hash string.
    mock._send = MagicMock(return_value="abcd" * 16)
    return mock


# --------------------------------------------------------------------------- #
# Constructor / ABI loading                                                   #
# --------------------------------------------------------------------------- #


def test_auction_client_constructs_with_mock_onchain() -> None:
    """AuctionClient can be built when an OnChainClient is injected."""

    mock = _build_mock_onchain()
    client = AuctionClient(onchain=mock)
    assert client.onchain is mock


def test_question_registry_constructs_with_mock_onchain() -> None:
    mock = _build_mock_onchain()
    client = QuestionRegistry(onchain=mock)
    # The class loads its ABI + creates its own contract handle from w3.
    mock.w3.eth.contract.assert_called_once()
    assert client.contract is not None


def test_builder_fee_router_constructs_with_mock_onchain() -> None:
    mock = _build_mock_onchain()
    client = BuilderFeeRouter(onchain=mock)
    mock.w3.eth.contract.assert_called_once()
    assert client.contract is not None


def test_reputation_registry_constructs_with_mock_onchain() -> None:
    mock = _build_mock_onchain()
    client = ReputationRegistryClient(onchain=mock)
    # Reputation client reuses the OnChainClient's reputation contract
    # rather than building its own.
    assert client.contract is mock.reputation


@pytest.mark.parametrize("contract_name", _CONTRACT_NAMES)
def test_abi_files_load_correctly(contract_name: str) -> None:
    """Each Foundry ABI artifact loads and is a non-empty list."""

    path = _FOUNDRY_OUT / f"{contract_name}.sol" / f"{contract_name}.json"
    assert path.exists(), f"missing ABI artifact: {path}"
    with path.open() as fh:
        abi = json.load(fh)["abi"]
    assert isinstance(abi, list) and abi, f"empty ABI for {contract_name}"


# --------------------------------------------------------------------------- #
# W11 — canonical eventId -> bytes32 encoder (bug C regression guard)         #
# --------------------------------------------------------------------------- #


def test_event_id_to_bytes32_is_deterministic_for_int() -> None:
    """Same int input must produce the same bytes32 (bug C reproducer guard)."""
    assert event_id_to_bytes32(216) == event_id_to_bytes32(216) == event_id_to_bytes32("216")


def test_event_id_to_bytes32_matches_auction_client_helper() -> None:
    """Dispatch (``_event_id_bytes``) and orchestrator (``event_id_to_bytes32``) must agree."""
    from polyglot_alpha.chain.auction_client import _event_id_bytes
    assert _event_id_bytes(216) == event_id_to_bytes32(216) and len(_event_id_bytes(216)) == 32


# --------------------------------------------------------------------------- #
# Auction: open / settle TX wiring                                            #
# --------------------------------------------------------------------------- #


def test_open_auction_returns_hex_tx_hash() -> None:
    """``open_auction`` should return a ``0x``-prefixed tx hash."""

    mock = _build_mock_onchain()
    # ``auction.functions.openAuction(...).build_transaction(...)`` must
    # return a dict so :meth:`_send` is callable on it.
    mock.auction.functions.openAuction.return_value.build_transaction.return_value = {
        "to": "0xdead", "data": "0x", "gas": 250_000,
    }

    tx_hash = asyncio.run(
        auction_mod.open_auction(event_id="event-xyz", content_hash=None, onchain=mock)
    )

    assert tx_hash.startswith("0x")
    assert len(tx_hash) == 66  # 0x + 64 hex chars
    mock.auction.functions.openAuction.assert_called_once()
    args, _ = mock.auction.functions.openAuction.call_args
    # First arg = eventId bytes32, second = eventHash bytes32.
    assert len(args[0]) == 32
    assert len(args[1]) == 32


def test_settle_auction_returns_hex_tx_hash() -> None:
    mock = _build_mock_onchain()
    mock.auction.functions.settleAuction.return_value.build_transaction.return_value = {
        "to": "0xdead", "data": "0x", "gas": 250_000,
    }

    winner = MagicMock(agent_address="0x" + "ab" * 20)
    tx_hash = asyncio.run(
        auction_mod.settle_auction(event_id="event-xyz", winner=winner, onchain=mock)
    )

    assert tx_hash.startswith("0x")
    mock.auction.functions.settleAuction.assert_called_once()


def test_open_auction_propagates_send_failure() -> None:
    """If the underlying ``_send`` raises, the exception bubbles up."""

    mock = _build_mock_onchain()
    mock.auction.functions.openAuction.return_value.build_transaction.return_value = {
        "to": "0xdead", "data": "0x", "gas": 250_000,
    }
    mock._send.side_effect = RuntimeError("execution reverted: AlreadyOpen")

    with pytest.raises(RuntimeError, match="AlreadyOpen"):
        asyncio.run(
            auction_mod.open_auction(
                event_id="event-fail", content_hash=None, onchain=mock
            )
        )


def test_open_auction_requires_operator_pk(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``HACKATHON_WALLET_PRIVATE_KEY`` is unset, we raise clearly."""

    monkeypatch.delenv("HACKATHON_WALLET_PRIVATE_KEY", raising=False)
    mock = _build_mock_onchain()

    with pytest.raises(RuntimeError, match="HACKATHON_WALLET_PRIVATE_KEY"):
        asyncio.run(
            auction_mod.open_auction(
                event_id="event-x", content_hash=None, onchain=mock
            )
        )


# --------------------------------------------------------------------------- #
# Auction: submit_bid / register_agent                                        #
# --------------------------------------------------------------------------- #


def test_submit_bid_calls_underlying_client_with_units() -> None:
    """``AuctionClient.submit_bid`` converts USDC float to base units."""

    mock = _build_mock_onchain()
    mock.submit_bid = MagicMock(return_value="cafe" * 16)
    client = AuctionClient(onchain=mock)

    tx_hash = asyncio.run(
        client.submit_bid(
            event_id="event-bid",
            bid_amount_usdc=1.25,
            candidate_hash=None,
            agent_pk=_AGENT_PK,
        )
    )

    assert tx_hash.startswith("0x")
    mock.submit_bid.assert_called_once()
    _, eid, units, chash = mock.submit_bid.call_args.args
    assert len(eid) == 32
    assert units == 1_250_000  # 1.25 USDC * 1e6
    assert len(chash) == 32


def test_register_agent_calls_register_after_approve() -> None:
    mock = _build_mock_onchain()
    mock.approve_usdc = MagicMock(return_value="deadbeef")
    mock.register_agent = MagicMock(return_value="cafebabe")
    client = AuctionClient(onchain=mock)

    tx_hash = asyncio.run(client.register_agent(_AGENT_PK, stake_usdc=5.0))

    assert tx_hash.startswith("0x")
    mock.approve_usdc.assert_called_once()
    mock.register_agent.assert_called_once()


def test_submit_bid_requires_agent_pk() -> None:
    mock = _build_mock_onchain()
    client = AuctionClient(onchain=mock)
    with pytest.raises(ValueError, match="agent_pk"):
        asyncio.run(
            client.submit_bid(
                event_id="x", bid_amount_usdc=1.0, candidate_hash=None, agent_pk=""
            )
        )


# --------------------------------------------------------------------------- #
# QuestionRegistry: commit_question                                           #
# --------------------------------------------------------------------------- #


def test_commit_question_returns_qid_and_tx_hash() -> None:
    mock = _build_mock_onchain()
    # The function builds its own contract via w3.eth.contract — patch
    # the returned MagicMock to drive ``registerQuestion`` + receipt path.
    fake_contract = MagicMock()
    fake_contract.functions.registerQuestion.return_value.build_transaction.return_value = {
        "to": "0xdead", "data": "0x", "gas": 350_000,
    }
    fake_contract.events.QuestionRegistered.return_value.process_receipt.return_value = [
        {"args": {"id": 42}}
    ]
    mock.w3.eth.contract = MagicMock(return_value=fake_contract)
    mock.w3.eth.wait_for_transaction_receipt = MagicMock(return_value={"status": 1})

    qid, tx_hash = asyncio.run(
        qr_mod.commit_question(
            event_id="event-q",
            candidate_hash="0x" + "a" * 64,
            builder_code="polyglot-alpha",
            pipeline_trace_ipfs=None,
            onchain=mock,
        )
    )

    assert qid.startswith("0x")
    assert tx_hash.startswith("0x")
    fake_contract.functions.registerQuestion.assert_called_once()


# --------------------------------------------------------------------------- #
# BuilderFeeRouter: record_fill / claim_fees                                  #
# --------------------------------------------------------------------------- #


def test_record_fill_converts_usdc_to_units() -> None:
    mock = _build_mock_onchain()
    fake_contract = MagicMock()
    fake_contract.functions.recordFill.return_value.build_transaction.return_value = {
        "to": "0xdead", "data": "0x", "gas": 250_000,
    }
    mock.w3.eth.contract = MagicMock(return_value=fake_contract)

    tx_hash = asyncio.run(
        builder_mod.record_fill(
            market_id="market-1",
            fill_amount_usdc=2.5,
            translator="0x" + "ab" * 20,
            onchain=mock,
        )
    )

    assert tx_hash.startswith("0x")
    fake_contract.functions.recordFill.assert_called_once()
    market_id, units, _translator = fake_contract.functions.recordFill.call_args.args
    assert market_id == "market-1"
    assert units == 2_500_000


def test_record_fill_with_split_emits_two_recordFill_calls() -> None:
    """Path A: split a builder fee 90/10 by emitting two on-chain TXs."""

    mock = _build_mock_onchain()
    fake_contract = MagicMock()
    fake_contract.functions.recordFill.return_value.build_transaction.return_value = {
        "to": "0xdead", "data": "0x", "gas": 250_000,
    }
    mock.w3.eth.contract = MagicMock(return_value=fake_contract)

    winner = "0x" + "11" * 20
    treasury = "0x" + "22" * 20

    result = asyncio.run(
        builder_mod.record_fill_with_split(
            market_id="market-split",
            fill_amount_usdc=1.0,
            winner=winner,
            treasury=treasury,
            onchain=mock,
        )
    )

    # Both legs should produce tx hashes.
    assert result["winner_tx"].startswith("0x")
    assert result["treasury_tx"].startswith("0x")
    # Two recordFill calls fired with the right split amounts.
    assert fake_contract.functions.recordFill.call_count == 2
    calls = fake_contract.functions.recordFill.call_args_list
    # First call should be the 90% winner leg.
    winner_args = calls[0].args
    treasury_args = calls[1].args
    assert winner_args[0] == "market-split"
    assert winner_args[1] == 900_000  # 0.9 USDC in 6-decimal units
    assert treasury_args[1] == 100_000  # 0.1 USDC in 6-decimal units
    # The amounts in the result dict match the leg credits.
    assert result["winner_amount"] == pytest.approx(0.9)
    assert result["treasury_amount"] == pytest.approx(0.1)


def test_record_fill_with_split_rejects_invalid_inputs() -> None:
    mock = _build_mock_onchain()
    # Zero fill -> ValueError.
    with pytest.raises(ValueError):
        asyncio.run(
            builder_mod.record_fill_with_split(
                market_id="m",
                fill_amount_usdc=0.0,
                winner="0x" + "11" * 20,
                treasury="0x" + "22" * 20,
                onchain=mock,
            )
        )


def test_claim_fees_calls_contract() -> None:
    mock = _build_mock_onchain()
    fake_contract = MagicMock()
    fake_contract.functions.claimFees.return_value.build_transaction.return_value = {
        "to": "0xdead", "data": "0x", "gas": 200_000,
    }
    mock.w3.eth.contract = MagicMock(return_value=fake_contract)

    tx_hash = asyncio.run(
        builder_mod.claim_fees(
            translator="0x" + "ab" * 20, onchain=mock
        )
    )

    assert tx_hash.startswith("0x")
    fake_contract.functions.claimFees.assert_called_once()


# --------------------------------------------------------------------------- #
# ReputationRegistry: update / get                                            #
# --------------------------------------------------------------------------- #


def test_register_agent_with_stake_emits_transfer_and_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path A anti-Sybil: USDC.transfer + ReputationRegistry.registerAgent."""

    monkeypatch.setenv("PLATFORM_TREASURY_ADDRESS", "0x" + "fe" * 20)

    mock = _build_mock_onchain()
    # USDC.transfer chain
    mock.usdc.functions.transfer.return_value.build_transaction.return_value = {
        "to": "0xusdc", "data": "0x", "gas": 120_000,
    }
    # ReputationRegistry.registerAgent
    mock.reputation.functions.registerAgent.return_value.build_transaction.return_value = {
        "to": "0xrep", "data": "0x", "gas": 200_000,
    }

    result = asyncio.run(
        rep_mod.register_agent_with_stake(
            operator_address="0x" + "ab" * 20,
            stake_usdc=100.0,
            onchain=mock,
        )
    )

    assert result["stake_tx"].startswith("0x")
    assert result["register_tx"].startswith("0x")
    assert result["stake_usdc"] == 100.0
    # USDC transfer to treasury for the stake.
    mock.usdc.functions.transfer.assert_called_once()
    transfer_args = mock.usdc.functions.transfer.call_args.args
    assert transfer_args[1] == 100_000_000  # 100 USDC in 6-decimal units
    # registerAgent called once.
    mock.reputation.functions.registerAgent.assert_called_once()


def test_update_reputation_sends_all_three_signals() -> None:
    mock = _build_mock_onchain()
    # Each ``updateOnX`` returns a build_transaction-able MagicMock.
    for fn in ("updateOnAuction", "updateOnQuality", "updateOnFee"):
        getattr(mock.reputation.functions, fn).return_value.build_transaction.return_value = {
            "to": "0xdead", "data": "0x", "gas": 200_000,
        }

    out = asyncio.run(
        rep_mod.update_reputation(
            agent="0x" + "ab" * 20,
            won=True,
            quality_passed=True,
            fee_usdc=0.5,
            onchain=mock,
        )
    )

    assert set(out.keys()) == {"auction", "quality", "fee"}
    for tx in out.values():
        assert tx.startswith("0x")
    mock.reputation.functions.updateOnAuction.assert_called_once()
    mock.reputation.functions.updateOnQuality.assert_called_once()
    mock.reputation.functions.updateOnFee.assert_called_once()


def test_get_reputation_reads_from_chain() -> None:
    mock = _build_mock_onchain()
    mock.get_reputation = MagicMock(return_value=1.42)

    rep = asyncio.run(
        rep_mod.get_reputation(agent="0x" + "ab" * 20, onchain=mock)
    )

    assert rep == pytest.approx(1.42)
    mock.get_reputation.assert_called_once()


def test_update_reputation_skips_unspecified_signals() -> None:
    mock = _build_mock_onchain()
    for fn in ("updateOnAuction", "updateOnQuality", "updateOnFee"):
        getattr(mock.reputation.functions, fn).return_value.build_transaction.return_value = {
            "to": "0xdead", "data": "0x", "gas": 200_000,
        }

    out = asyncio.run(
        rep_mod.update_reputation(
            agent="0x" + "ab" * 20, quality_passed=False, onchain=mock
        )
    )

    assert list(out.keys()) == ["quality"]
    mock.reputation.functions.updateOnAuction.assert_not_called()
    mock.reputation.functions.updateOnFee.assert_not_called()
