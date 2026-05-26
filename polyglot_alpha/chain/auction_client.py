"""Real ``TranslationAuction`` adapter.

The orchestrator imports module-level ``open_auction`` / ``collect_bids`` /
``settle_auction`` (legacy shape) so we expose both that surface and a
class-based :class:`AuctionClient` for callers that want explicit lifetime
management.

All write calls are signed with the operator wallet
(``HACKATHON_WALLET_PRIVATE_KEY``); bid submissions during ``collect_bids``
are observed via ``BidSubmitted`` event logs filtered by ``eventId``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount

from ..onchain import (
    OnChainClient,
    event_id_from_event,
    send_with_nonce_lock,
    units_to_usdc,
    usdc_to_units,
)

logger = logging.getLogger(__name__)


# bytes32(0) — used as the deterministic ``eventHash`` salt when the
# orchestrator already encodes its event content into ``eventId``. The
# contract accepts any bytes32 here; it is just stored on-chain for audit.
_ZERO_HASH = b"\x00" * 32

DEFAULT_GAS_OPEN = 250_000
DEFAULT_GAS_SETTLE = 250_000


def _operator_account() -> LocalAccount:
    pk = os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
    if not pk:
        raise RuntimeError(
            "HACKATHON_WALLET_PRIVATE_KEY not set; required for "
            "chain.auction_client operator writes"
        )
    return Account.from_key(pk)


def _event_id_bytes(event_id: Any) -> bytes:
    """Coerce a sqlite int or string to the contract's ``bytes32 eventId``."""

    if isinstance(event_id, (bytes, bytearray)):
        if len(event_id) == 32:
            return bytes(event_id)
        return event_id_from_event(event_id.hex() if isinstance(event_id, (bytes, bytearray)) else str(event_id))
    return event_id_from_event(str(event_id))


def _content_hash_bytes(content_hash: Optional[str]) -> bytes:
    if not content_hash:
        return _ZERO_HASH
    raw = content_hash[2:] if content_hash.startswith("0x") else content_hash
    try:
        as_bytes = bytes.fromhex(raw)
    except ValueError:
        # Free-form string: hash to bytes32 using keccak so the on-chain
        # field is always 32 bytes.
        return event_id_from_event(content_hash)
    if len(as_bytes) == 32:
        return as_bytes
    # Pad short hex digests up to 32 bytes (left-pad).
    if len(as_bytes) < 32:
        return as_bytes.rjust(32, b"\x00")
    return as_bytes[:32]


@dataclass
class _ChainBidRecord:
    agent_address: str
    bid_amount: float
    candidate_hash: str
    tx_hash: str
    reputation: float = 1.0
    stake_amount: float = 5.0


class AuctionClient:
    """Object-style facade. Stateful w.r.t. its :class:`OnChainClient`."""

    def __init__(self, *, onchain: Optional[OnChainClient] = None) -> None:
        self._onchain = onchain or OnChainClient()

    @property
    def onchain(self) -> OnChainClient:
        return self._onchain

    async def open_auction(self, event_id: Any, content_hash: Optional[str]) -> str:
        return await open_auction(event_id, content_hash, onchain=self._onchain)

    async def collect_bids(
        self,
        event_id: Any,
        window_seconds: float,
        *,
        poll_interval_s: float = 1.5,
    ) -> list[_ChainBidRecord]:
        return await collect_bids(
            event_id,
            window_seconds,
            onchain=self._onchain,
            poll_interval_s=poll_interval_s,
        )

    async def settle_auction(self, event_id: Any, winner: Any) -> str:
        return await settle_auction(event_id, winner, onchain=self._onchain)

    async def submit_bid(
        self,
        event_id: Any,
        bid_amount_usdc: float,
        candidate_hash: Optional[str],
        agent_pk: str,
    ) -> str:
        """Sign + send ``submitBid`` from the given agent private key.

        Convenience wrapper over :meth:`OnChainClient.submit_bid` that
        accepts a USDC float (we convert to base units) and a free-form
        hex ``candidate_hash`` (we pad/keccak to bytes32).
        """

        if not agent_pk:
            raise ValueError("agent_pk is required to sign submitBid")
        account = Account.from_key(agent_pk)
        eid = _event_id_bytes(event_id)
        chash = _content_hash_bytes(candidate_hash)
        amount_units = usdc_to_units(max(0.0, bid_amount_usdc))

        def _send() -> str:
            return self._onchain.submit_bid(account, eid, amount_units, chash)

        tx_hash = await send_with_nonce_lock(account, _send)
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        logger.info(
            "submitBid(event_id=%s, amount=%.4f, agent=%s) tx=%s",
            event_id,
            bid_amount_usdc,
            account.address,
            tx_hash,
        )
        return tx_hash

    async def register_agent(
        self,
        agent_pk: str,
        stake_usdc: float = 5.0,
    ) -> str:
        """Run the one-shot agent-registration flow.

        Approves the auction contract for ``stake_usdc`` USDC and then
        calls ``registerAgent()``. Returns the ``registerAgent`` tx hash.
        """

        if not agent_pk:
            raise ValueError("agent_pk is required to sign registerAgent")
        account = Account.from_key(agent_pk)
        stake_units = usdc_to_units(max(0.0, stake_usdc))

        def _send() -> str:
            # Best-effort approve; ignore failure (already approved).
            try:
                self._onchain.approve_usdc(account, stake_units)
            except Exception:  # pragma: no cover - best-effort
                logger.exception("register_agent: approve_usdc failed; continuing")
            return self._onchain.register_agent(account)

        tx_hash = await send_with_nonce_lock(account, _send)
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        logger.info(
            "registerAgent(agent=%s, stake=%.4f) tx=%s",
            account.address,
            stake_usdc,
            tx_hash,
        )
        return tx_hash

    async def withdraw_stake(self, agent_pk: str) -> str:
        """Call ``withdrawStake()`` once the unlock window has elapsed."""

        if not agent_pk:
            raise ValueError("agent_pk is required to sign withdrawStake")
        account = Account.from_key(agent_pk)

        def _send() -> str:
            base = self._onchain._build_base_txn(account)
            txn = self._onchain.auction.functions.withdrawStake().build_transaction(
                {**base, "gas": 150_000}
            )
            return self._onchain._send(txn, account)

        tx_hash = await send_with_nonce_lock(account, _send)
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        logger.info("withdrawStake(agent=%s) tx=%s", account.address, tx_hash)
        return tx_hash


# ---------------------------------------------------------------------------
# Module-level functions (orchestrator imports these)
# ---------------------------------------------------------------------------


async def open_auction(
    event_id: Any,
    content_hash: Optional[str],
    *,
    onchain: Optional[OnChainClient] = None,
) -> str:
    """Open an on-chain auction. Returns the tx hash (0x...)."""

    client = onchain or OnChainClient()
    account = _operator_account()
    eid = _event_id_bytes(event_id)
    ehash = _content_hash_bytes(content_hash)

    def _send() -> str:
        base = client._build_base_txn(account)
        txn = client.auction.functions.openAuction(eid, ehash).build_transaction(
            {**base, "gas": DEFAULT_GAS_OPEN}
        )
        return client._send(txn, account)

    tx_hash = await send_with_nonce_lock(account, _send)
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    logger.info("openAuction(event_id=%s) tx=%s", event_id, tx_hash)
    return tx_hash


async def collect_bids(
    event_id: Any,
    window_seconds: float,
    *,
    onchain: Optional[OnChainClient] = None,
    poll_interval_s: float = 1.5,
) -> list[_ChainBidRecord]:
    """Observe ``BidSubmitted`` events for ``event_id`` for the window.

    Reads logs from the auction contract (filtered by indexed ``eventId``)
    every ``poll_interval_s`` until ``window_seconds`` have elapsed.
    Returns one :class:`_ChainBidRecord` per unique bidder (the contract
    keeps only the last bid per address; we mirror that here).
    """

    client = onchain or OnChainClient()
    eid = _event_id_bytes(event_id)
    loop = asyncio.get_running_loop()

    start_block = await loop.run_in_executor(
        None, lambda: client.w3.eth.block_number
    )
    deadline = asyncio.get_running_loop().time() + max(window_seconds, 0.0)
    seen: dict[str, _ChainBidRecord] = {}

    def _poll(from_block: int) -> tuple[list[Any], int]:
        latest = client.w3.eth.block_number
        if latest < from_block:
            return [], from_block
        flt = client.auction.events.BidSubmitted.create_filter(
            from_block=from_block,
            to_block=latest,
            argument_filters={"eventId": eid},
        )
        entries = flt.get_all_entries()
        return entries, latest + 1

    from_block = start_block
    while asyncio.get_running_loop().time() < deadline:
        try:
            entries, from_block = await loop.run_in_executor(
                None, _poll, from_block
            )
        except Exception:  # pragma: no cover - RPC noise
            logger.exception("collect_bids: poll iteration failed")
            entries = []
        for entry in entries:
            args = getattr(entry, "args", None) or entry["args"]
            bidder = str(args["bidder"])
            bid_units = int(args["bidAmount"])
            candidate_hash_bytes = bytes(args["candidateHash"])
            tx_hash = entry.transactionHash.hex() if hasattr(entry, "transactionHash") else (
                entry["transactionHash"].hex() if isinstance(entry, dict) else ""
            )
            if not tx_hash.startswith("0x"):
                tx_hash = "0x" + tx_hash
            # Pull live reputation (best-effort) so the orchestrator can
            # apply the same min-rep gate it uses for mock bids.
            try:
                rep = await loop.run_in_executor(
                    None, client.get_reputation, bidder
                )
            except Exception:  # pragma: no cover
                rep = 1.0
            seen[bidder] = _ChainBidRecord(
                agent_address=bidder,
                bid_amount=units_to_usdc(bid_units),
                candidate_hash=candidate_hash_bytes.hex(),
                tx_hash=tx_hash,
                reputation=float(rep) if rep else 1.0,
            )
        await asyncio.sleep(poll_interval_s)

    logger.info(
        "collect_bids: event=%s observed_bidders=%d window=%.1fs",
        event_id,
        len(seen),
        window_seconds,
    )
    return list(seen.values())


async def settle_auction(
    event_id: Any,
    winner: Any,
    *,
    onchain: Optional[OnChainClient] = None,
) -> str:
    """Call ``settleAuction(eventId)``. Returns the tx hash."""

    client = onchain or OnChainClient()
    account = _operator_account()
    eid = _event_id_bytes(event_id)

    def _send() -> str:
        base = client._build_base_txn(account)
        txn = client.auction.functions.settleAuction(eid).build_transaction(
            {**base, "gas": DEFAULT_GAS_SETTLE}
        )
        return client._send(txn, account)

    tx_hash = await send_with_nonce_lock(account, _send)
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    winner_addr = getattr(winner, "agent_address", str(winner))
    logger.info(
        "settleAuction(event_id=%s) tx=%s winner=%s", event_id, tx_hash, winner_addr
    )
    return tx_hash


__all__ = [
    "AuctionClient",
    "collect_bids",
    "open_auction",
    "settle_auction",
]
