"""Thin web3.py wrapper for the Arc-testnet PolyglotAlpha contracts.

Centralises:

* Contract address resolution from ``.env`` (with sensible defaults).
* ABI loading from the Foundry ``out/`` directory.
* Wallet helpers (``LocalAccount`` construction, signed-tx submission).
* Common reads (``getReputation``) and writes (``registerAgent``,
  ``submitBid``).

All write functions are synchronous because web3.py 7.x is sync-only over
HTTP; agents call them via ``loop.run_in_executor`` to keep their asyncio
event loop responsive.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract import Contract


# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FOUNDRY_OUT = _REPO_ROOT / "contracts" / "out"

# 5 USDC, where the deployed MockUSDC uses 6 decimals.
USDC_DECIMALS = 6
REGISTRATION_STAKE_USDC = 5.0
AUCTION_WINDOW_SECONDS = 60


# ---------------------------------------------------------------------------
# Addresses (env-overridable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainConfig:
    rpc_url: str
    chain_id: int
    translation_auction: str
    reputation_registry: str
    question_registry: str
    builder_fee_router: str
    usdc: str

    @classmethod
    def from_env(cls) -> "ChainConfig":
        return cls(
            rpc_url=os.environ.get("ARC_TESTNET_RPC", "https://rpc.testnet.arc.network"),
            chain_id=int(os.environ.get("ARC_CHAIN_ID", "5042002")),
            translation_auction=os.environ.get(
                "TRANSLATION_AUCTION_ADDRESS",
                "0xF182ab950688B9553B55e28fcb5f34dDFEa2038b",
            ),
            reputation_registry=os.environ.get(
                "REPUTATION_REGISTRY_ADDRESS",
                "0x5aC7be7c5501640c221273813076DF13480C4C5A",
            ),
            question_registry=os.environ.get(
                "QUESTION_REGISTRY_ADDRESS",
                "0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1",
            ),
            builder_fee_router=os.environ.get(
                "BUILDER_FEE_ROUTER_ADDRESS",
                "0xb934662702Cd9e16E3F1f9D80C72Bc94B7FFF3d3",
            ),
            usdc=os.environ.get(
                "ARC_TESTNET_USDC_ADDRESS",
                "0x477fC4C3DcC87C3Ceb13adc931F6bBeDAcCa391D",
            ),
        )


# ---------------------------------------------------------------------------
# ABI loading
# ---------------------------------------------------------------------------


# Minimal ERC20 ABI used when MockUSDC artifact is not present in
# ``contracts/out`` (the Foundry build only emits the four production
# contracts plus IERC20; the test-only MockUSDC.sol is excluded).
_ERC20_ABI: list[Dict[str, Any]] = [
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "owner", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "approve",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "allowance",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "transfer",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "transferFrom",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "mint",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "decimals",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
]


def _load_abi(contract_name: str) -> list[Dict[str, Any]]:
    """Load the ABI from the Foundry ``out/`` artifact tree.

    Falls back to a built-in minimal ERC20 ABI for ``MockUSDC`` when the
    artifact is missing (the test-only contract is excluded from the
    production Foundry build but agents still need to call
    ``approve``/``balanceOf`` on the deployed USDC token).
    """

    path = _FOUNDRY_OUT / f"{contract_name}.sol" / f"{contract_name}.json"
    if not path.exists() and contract_name == "MockUSDC":
        return _ERC20_ABI
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)["abi"]


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def usdc_to_units(amount_usdc: float, decimals: int = USDC_DECIMALS) -> int:
    """Convert a float USDC amount to integer base units."""

    if amount_usdc < 0:
        raise ValueError("USDC amount must be non-negative")
    return int(round(amount_usdc * (10 ** decimals)))


def units_to_usdc(amount_units: int, decimals: int = USDC_DECIMALS) -> float:
    return amount_units / (10 ** decimals)


def reputation_to_float(raw: int, *, scale: int = 10**18) -> float:
    """Convert a 1e18-scaled reputation integer to a Python float."""

    return raw / scale


def event_id_from_event(event_id_str: str) -> bytes:
    """Coerce a hex/string event id into bytes32. Accepts either a 0x-prefixed
    32-byte hex string or a free-form string that will be keccak-hashed."""

    if event_id_str.startswith("0x") and len(event_id_str) == 66:
        return bytes.fromhex(event_id_str[2:])
    return Web3.keccak(text=event_id_str)


# ---------------------------------------------------------------------------
# OnChain client
# ---------------------------------------------------------------------------


class OnChainClient:
    """Synchronous web3 client; agents wrap calls in run_in_executor."""

    def __init__(self, config: Optional[ChainConfig] = None, *, w3: Optional[Web3] = None) -> None:
        self.config = config or ChainConfig.from_env()
        self.w3 = w3 or Web3(Web3.HTTPProvider(self.config.rpc_url))
        self.auction: Contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.config.translation_auction),
            abi=_load_abi("TranslationAuction"),
        )
        self.reputation: Contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.config.reputation_registry),
            abi=_load_abi("ReputationRegistry"),
        )
        self.usdc: Contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.config.usdc),
            abi=_load_abi("MockUSDC"),
        )

    # ----- account helpers -------------------------------------------------

    @staticmethod
    def account_from_pk(private_key: str) -> LocalAccount:
        return Account.from_key(private_key)

    # ----- reads -----------------------------------------------------------

    def get_reputation(self, address: str) -> float:
        raw = self.reputation.functions.getReputation(
            Web3.to_checksum_address(address)
        ).call()
        return reputation_to_float(raw)

    def is_registered(self, address: str) -> bool:
        return bool(
            self.auction.functions.registered(Web3.to_checksum_address(address)).call()
        )

    # ----- writes ----------------------------------------------------------

    def _send(self, txn: Dict[str, Any], account: LocalAccount) -> str:
        signed = self.w3.eth.account.sign_transaction(txn, account.key)
        # web3.py 7.x renamed the attribute from rawTransaction to raw_transaction.
        raw_tx = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = self.w3.eth.send_raw_transaction(raw_tx)
        return tx_hash.hex()

    def _build_base_txn(self, account: LocalAccount) -> Dict[str, Any]:
        return {
            "from": account.address,
            "nonce": self.w3.eth.get_transaction_count(account.address),
            "chainId": self.config.chain_id,
            "gasPrice": self.w3.eth.gas_price,
        }

    def register_agent(self, account: LocalAccount) -> str:
        base = self._build_base_txn(account)
        txn = self.auction.functions.registerAgent().build_transaction(
            {**base, "gas": 200_000}
        )
        return self._send(txn, account)

    def approve_usdc(self, account: LocalAccount, amount_units: int) -> str:
        base = self._build_base_txn(account)
        txn = self.usdc.functions.approve(
            self.auction.address, amount_units
        ).build_transaction({**base, "gas": 80_000})
        return self._send(txn, account)

    def submit_bid(
        self,
        account: LocalAccount,
        event_id: bytes,
        bid_amount_units: int,
        candidate_hash: bytes,
    ) -> str:
        base = self._build_base_txn(account)
        txn = self.auction.functions.submitBid(
            event_id, bid_amount_units, candidate_hash
        ).build_transaction({**base, "gas": 250_000})
        return self._send(txn, account)
