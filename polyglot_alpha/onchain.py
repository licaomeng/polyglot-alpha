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

import asyncio
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

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


def event_id_to_bytes32(event_id: Any) -> bytes:
    """Canonical ``bytes32 eventId`` encoder shared by every chain call site.

    The TranslationAuction contract stores per-auction state under
    ``mapping(bytes32 => Auction) auctions``. Every ``submitBid`` /
    ``getBid`` / ``settleAuction`` / ``openAuction`` call MUST pass the
    same bytes32 derived from a given Python ``event_id`` or the contract
    will read/write the wrong storage slot — which manifests as
    ``getBid(eventId, bidder) -> 0`` even though the bid tx mined with
    ``status=1`` (bug C, W10-4 stress audit).

    Accepts:
      * ``bytes`` / ``bytearray`` of length 32 — returned verbatim.
      * Any other ``bytes`` / ``bytearray`` — hashed to bytes32 via
        :func:`event_id_from_event` (after hex-decoding).
      * Anything else — string-coerced and fed to
        :func:`event_id_from_event` (which keccak-hashes the decimal
        representation; ``"216"`` -> ``keccak("216")``).
    """

    if isinstance(event_id, (bytes, bytearray)):
        if len(event_id) == 32:
            return bytes(event_id)
        return event_id_from_event(bytes(event_id).hex())
    return event_id_from_event(str(event_id))


# ---------------------------------------------------------------------------
# Per-wallet nonce serialization
# ---------------------------------------------------------------------------
#
# Production incident (event 137, settleAuction): two concurrent triggers
# both read ``getTransactionCount(pending) == 156`` and then both submitted
# a TX with nonce 156, causing Arc to reject the second one with
# ``nonce too low: next nonce 157, tx nonce 156``.
#
# Fix: serialize the read-nonce -> build-tx -> send_raw_transaction sequence
# for every TX signed by the same wallet address. The lock is keyed by the
# *checksum address* (the operator wallet is shared across all 6 lifecycle
# phases, so one shared key collapses to one shared lock).
#
# The registry is **module-level** so that distinct ``OnChainClient``
# instances spun up by different services (auction / question / reputation /
# fee-router) still share the same lock for the same operator wallet. An
# instance-level lock would not protect against this case — that is the
# whole point of the production failure.
#
# We lazily create one ``asyncio.Lock`` per address on first use; the
# ``_REGISTRY_GUARD`` ``threading.Lock`` only protects insertion into the
# dict so two coroutines starting at the same time cannot create two
# different ``asyncio.Lock`` objects for the same address.

_NONCE_LOCKS: Dict[str, "asyncio.Lock"] = {}
_REGISTRY_GUARD = threading.Lock()

# Tracks the last nonce we *handed out* per address, even if the Arc
# sequencer / RPC has not yet surfaced the prior TX in its mempool view.
# Used in tandem with ``eth_getTransactionCount(addr, "pending")``: we take
# ``max(chain_pending, _LAST_USED_NONCE[addr] + 1)`` so a slow RPC cannot
# hand back a stale nonce when multiple TXs are submitted in rapid
# succession from the same wallet. Guarded by the per-address asyncio.Lock
# from ``nonce_lock_for`` (which serializes the build-tx -> send sequence
# within this process) plus ``_REGISTRY_GUARD`` for initial dict access.
_LAST_USED_NONCE: Dict[str, int] = {}


def nonce_lock_for(address: str) -> "asyncio.Lock":
    """Return the shared ``asyncio.Lock`` for ``address`` (checksum-normalized).

    Module-level + keyed by address so all ``OnChainClient`` instances
    coordinate on the same wallet.
    """

    key = Web3.to_checksum_address(address)
    lock = _NONCE_LOCKS.get(key)
    if lock is not None:
        return lock
    with _REGISTRY_GUARD:
        lock = _NONCE_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _NONCE_LOCKS[key] = lock
        return lock


async def send_with_nonce_lock(
    account: LocalAccount,
    blocking_send: Callable[[], Any],
) -> Any:
    """Run ``blocking_send`` in the default executor while holding the
    per-wallet nonce lock for ``account.address``.

    ``blocking_send`` MUST encapsulate the full read-nonce -> build-tx ->
    ``send_raw_transaction`` sequence (i.e. it calls
    ``_build_base_txn`` + ``_send``). The lock is released only after the
    raw TX has been broadcast, which is when the next nonce becomes
    observable to other tasks reading ``getTransactionCount(pending)``.
    """

    lock = nonce_lock_for(account.address)
    loop = asyncio.get_running_loop()
    async with lock:
        return await loop.run_in_executor(None, blocking_send)


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

    def _next_nonce(self, address: str) -> int:
        """Return the nonce to use for the next TX from ``address``.

        Reads ``eth_getTransactionCount(addr, "pending")`` so in-mempool
        TXs from the same wallet are accounted for, then takes the max
        with our locally tracked ``_LAST_USED_NONCE + 1`` to guard
        against RPCs that lag behind their own mempool. The chosen nonce
        is recorded in ``_LAST_USED_NONCE`` for the next call.

        Callers must already hold ``nonce_lock_for(address)`` so the
        read-then-record sequence is atomic within the process.
        """

        key = Web3.to_checksum_address(address)
        chain_pending = self.w3.eth.get_transaction_count(key, "pending")
        last_used = _LAST_USED_NONCE.get(key, -1)
        nonce = max(chain_pending, last_used + 1)
        _LAST_USED_NONCE[key] = nonce
        return nonce

    def _build_base_txn(self, account: LocalAccount) -> Dict[str, Any]:
        return {
            "from": account.address,
            "nonce": self._next_nonce(account.address),
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
