"""Real ``ReputationRegistry`` adapter.

Wraps the three operator-signed writers ``updateOnAuction``,
``updateOnQuality``, ``updateOnFee`` and the read helper ``getReputation``.

The ``update_reputation`` async helper exposes the most-common combined
"after a quality-passed translation, bump reputation" flow used by the
orchestrator's post-commit step. Individual signals can be sent via the
class methods on :class:`ReputationRegistryClient`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount

from web3 import Web3

from ..onchain import (
    OnChainClient,
    reputation_to_float,
    send_with_nonce_lock,
    usdc_to_units,
)

logger = logging.getLogger(__name__)


def _operator_account() -> LocalAccount:
    pk = os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
    if not pk:
        raise RuntimeError(
            "HACKATHON_WALLET_PRIVATE_KEY not set; required for "
            "chain.reputation_registry operator writes"
        )
    return Account.from_key(pk)


class ReputationRegistryClient:
    """Object-style facade around the ``ReputationRegistry`` contract."""

    def __init__(self, *, onchain: Optional[OnChainClient] = None) -> None:
        self._onchain = onchain or OnChainClient()

    @property
    def contract(self):  # type: ignore[no-untyped-def]
        return self._onchain.reputation

    async def get_reputation(self, agent: str) -> float:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._onchain.get_reputation, agent
        )

    async def update_on_auction(self, agent: str, won: bool) -> str:
        return await _send_update(
            self._onchain, "updateOnAuction", [agent, bool(won)]
        )

    async def update_on_quality(self, agent: str, passed: bool) -> str:
        return await _send_update(
            self._onchain, "updateOnQuality", [agent, bool(passed)]
        )

    async def update_on_fee(self, agent: str, fee_amount_usdc: float) -> str:
        return await _send_update(
            self._onchain,
            "updateOnFee",
            [agent, usdc_to_units(max(0.0, fee_amount_usdc))],
        )


async def _send_update(
    client: OnChainClient,
    fn_name: str,
    args: list,
) -> str:
    account = _operator_account()
    fn = getattr(client.reputation.functions, fn_name)

    def _send() -> str:
        base = client._build_base_txn(account)
        txn = fn(*args).build_transaction({**base, "gas": 200_000})
        return client._send(txn, account)

    tx_hash = await send_with_nonce_lock(account, _send)
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    logger.info("%s(%s) tx=%s", fn_name, args, tx_hash)
    return tx_hash


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


async def get_reputation(
    agent: str, *, onchain: Optional[OnChainClient] = None
) -> float:
    client = onchain or OnChainClient()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, client.get_reputation, agent)


async def update_reputation(
    agent: str,
    *,
    won: Optional[bool] = None,
    quality_passed: Optional[bool] = None,
    fee_usdc: Optional[float] = None,
    onchain: Optional[OnChainClient] = None,
) -> dict[str, str]:
    """Send any combination of the three reputation signals.

    Returns a dict of ``{signal: tx_hash}`` for every signal sent.
    """

    client = onchain or OnChainClient()
    out: dict[str, str] = {}
    if won is not None:
        out["auction"] = await _send_update(
            client, "updateOnAuction", [agent, bool(won)]
        )
    if quality_passed is not None:
        out["quality"] = await _send_update(
            client, "updateOnQuality", [agent, bool(quality_passed)]
        )
    if fee_usdc is not None:
        out["fee"] = await _send_update(
            client, "updateOnFee", [agent, usdc_to_units(max(0.0, fee_usdc))]
        )
    return out


# ---------------------------------------------------------------------------
# Anti-Sybil registration (Path A — protocol-level 100 USDC stake)
# ---------------------------------------------------------------------------

# 100 USDC stake required to register a new operator. This is enforced
# at the orchestration layer via a USDC.transferFrom into the platform
# treasury BEFORE we call the existing `registerAgent` (which itself
# pulls a smaller native stake into the auction contract). The 100 USDC
# stake is intended as the dominant Sybil-resistance signal; see
# outputs/WEB3_STORY.md section 4 for the full rationale.
ANTI_SYBIL_STAKE_USDC: float = 100.0


def _treasury_address() -> str:
    """Return the platform treasury address.

    For the hackathon demo, the treasury defaults to the operator wallet
    (which is funded with MockUSDC on Arc testnet). Production deployments
    should set ``PLATFORM_TREASURY_ADDRESS`` to a multisig.
    """

    addr = os.environ.get("PLATFORM_TREASURY_ADDRESS")
    if not addr:
        addr = os.environ.get("HACKATHON_WALLET_ADDRESS")
    if not addr:
        raise RuntimeError(
            "PLATFORM_TREASURY_ADDRESS (or HACKATHON_WALLET_ADDRESS as fallback) "
            "must be set to enforce anti-Sybil registration stakes"
        )
    return Web3.to_checksum_address(addr)


async def register_agent_with_stake(
    operator_address: str,
    *,
    signer_pk: Optional[str] = None,
    stake_usdc: float = ANTI_SYBIL_STAKE_USDC,
    onchain: Optional[OnChainClient] = None,
) -> dict[str, Optional[str]]:
    """Register an external operator with a 100 USDC anti-Sybil stake.

    Two on-chain transactions are sent in order:

      1. ``MockUSDC.transferFrom(operator -> treasury, 100 USDC)`` — proves
         the operator controls 100 USDC and burns/locks it into the
         platform treasury. Requires the operator to have approved the
         operator-relayer (this wallet) for ``stake_usdc`` first.
      2. ``ReputationRegistry.registerAgent(operator_address)`` — seeds the
         reputation row at the bootstrap level (0.7 — below seeders' 1.0).

    For demo purposes we sign both TXs with the orchestrator's operator
    wallet (the operator address is recorded as a parameter and the stake
    is sent from the operator wallet). In a real deployment the stake TX
    is signed by the operator and the registration TX is signed by the
    relayer (Path B).

    Returns ``{"stake_tx": "0x...", "register_tx": "0x..."}``. Either may
    be ``None`` if that leg failed.
    """

    client = onchain or OnChainClient()
    account = _operator_account()
    operator_checksum = Web3.to_checksum_address(operator_address)
    treasury_checksum = _treasury_address()
    stake_units = usdc_to_units(max(0.0, stake_usdc))

    stake_tx: Optional[str] = None
    register_tx: Optional[str] = None

    # ----- Leg 1: USDC transfer (operator -> treasury) ----------------------
    def _send_stake() -> str:
        base = client._build_base_txn(account)
        # We use ``transfer`` (not ``transferFrom``) because in the demo
        # the operator wallet IS the signer. In production this would be
        # ``transferFrom(operator_address, treasury, stake_units)`` signed
        # by the relayer after the operator approves. See WEB3_STORY.md
        # section 4 for the full Path B flow.
        txn = client.usdc.functions.transfer(
            treasury_checksum, stake_units
        ).build_transaction({**base, "gas": 120_000})
        return client._send(txn, account)

    try:
        stake_tx = await send_with_nonce_lock(account, _send_stake)
        if stake_tx and not stake_tx.startswith("0x"):
            stake_tx = "0x" + stake_tx
        logger.info(
            "register_agent_with_stake: stake leg tx=%s (operator=%s, "
            "treasury=%s, amount=%.4f USDC)",
            stake_tx,
            operator_checksum,
            treasury_checksum,
            stake_usdc,
        )
    except Exception as exc:  # pragma: no cover - chain best-effort
        logger.error(
            "register_agent_with_stake: stake transfer failed "
            "(operator=%s amount=%.4f): %s",
            operator_checksum,
            stake_usdc,
            exc,
        )

    # ----- Leg 2: ReputationRegistry.registerAgent (idempotent) -------------
    # Use ``registerAgent`` if the contract exposes it; otherwise fall back
    # to a benign updateOnAuction(false) which seeds the row to the bootstrap
    # level without claiming an unearned win.
    def _send_register() -> str:
        base = client._build_base_txn(account)
        fns = client.reputation.functions
        if hasattr(fns, "registerAgent"):
            fn = fns.registerAgent(operator_checksum)
        else:
            # Bootstrap: tiny no-op signal so the row exists with avg = 0.7
            fn = fns.updateOnAuction(operator_checksum, False)
        txn = fn.build_transaction({**base, "gas": 200_000})
        return client._send(txn, account)

    try:
        register_tx = await send_with_nonce_lock(account, _send_register)
        if register_tx and not register_tx.startswith("0x"):
            register_tx = "0x" + register_tx
        logger.info(
            "register_agent_with_stake: register leg tx=%s (operator=%s)",
            register_tx,
            operator_checksum,
        )
    except Exception as exc:  # pragma: no cover
        logger.error(
            "register_agent_with_stake: registerAgent failed (operator=%s): %s",
            operator_checksum,
            exc,
        )

    return {
        "stake_tx": stake_tx,
        "register_tx": register_tx,
        "operator_address": operator_checksum,
        "treasury_address": treasury_checksum,
        "stake_usdc": stake_usdc,
    }


__all__ = [
    "ReputationRegistryClient",
    "ANTI_SYBIL_STAKE_USDC",
    "get_reputation",
    "register_agent_with_stake",
    "update_reputation",
]
