"""Real ``BuilderFeeRouter`` adapter.

Exposes the two operator-facing functions the orchestrator and fill
listener need:

* :func:`record_fill` — credit a translator with their share of a fill
  notional (operator-signed; the on-chain contract restricts callers).
* :func:`claim_fees` — pull accrued claimable USDC for a translator.

A small :class:`BuilderFeeRouter` class is also exposed so callers that
want explicit lifetime management can hold onto the contract handle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3

from ..onchain import OnChainClient, send_with_nonce_lock, usdc_to_units

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FOUNDRY_OUT = _REPO_ROOT / "contracts" / "out"

_BUILDER_FEE_ROUTER_ABI_PATH = (
    _FOUNDRY_OUT / "BuilderFeeRouter.sol" / "BuilderFeeRouter.json"
)


def _operator_account() -> LocalAccount:
    pk = os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
    if not pk:
        raise RuntimeError(
            "HACKATHON_WALLET_PRIVATE_KEY not set; required for "
            "chain.builder_fee_router operator writes"
        )
    return Account.from_key(pk)


def _load_abi() -> list[dict[str, Any]]:
    with _BUILDER_FEE_ROUTER_ABI_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)["abi"]


def _router_address() -> str:
    return os.environ.get(
        "BUILDER_FEE_ROUTER_ADDRESS",
        "0xcE7596d9b21333Eae441E912699514F6fBD150e5",
    )


class BuilderFeeRouter:
    """Object-style facade. Stateful w.r.t. its OnChainClient."""

    def __init__(self, *, onchain: Optional[OnChainClient] = None) -> None:
        client = onchain or OnChainClient()
        self._onchain = client
        self._contract = client.w3.eth.contract(
            address=Web3.to_checksum_address(_router_address()),
            abi=_load_abi(),
        )

    @property
    def contract(self):  # type: ignore[no-untyped-def]
        return self._contract

    async def record_fill(
        self,
        market_id: str,
        fill_amount_usdc: float,
        translator: str,
    ) -> str:
        return await record_fill(
            market_id,
            fill_amount_usdc,
            translator,
            onchain=self._onchain,
        )

    async def claim_fees(self, translator: str) -> str:
        return await claim_fees(translator, onchain=self._onchain)

    async def fund(self, amount_usdc: float) -> str:
        """Operator-funded top-up of the router's USDC reserve.

        Approves the router for ``amount_usdc`` and calls ``fund(uint256)``.
        Returns the ``fund`` tx hash.
        """

        account = _operator_account()
        amount_units = usdc_to_units(max(0.0, amount_usdc))

        def _send() -> str:
            # Approve the router to pull USDC from the operator wallet.
            try:
                base = self._onchain._build_base_txn(account)
                approve_txn = self._onchain.usdc.functions.approve(
                    self._contract.address, amount_units
                ).build_transaction({**base, "gas": 80_000})
                self._onchain._send(approve_txn, account)
            except Exception:  # pragma: no cover - best-effort
                logger.exception("fund: approve failed; continuing")
            base = self._onchain._build_base_txn(account)
            txn = self._contract.functions.fund(amount_units).build_transaction(
                {**base, "gas": 200_000}
            )
            return self._onchain._send(txn, account)

        tx_hash = await send_with_nonce_lock(account, _send)
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        logger.info("fund(amount=%.4f) tx=%s", amount_usdc, tx_hash)
        return tx_hash

    async def get_cumulative_fees(self, translator: str) -> float:
        loop = asyncio.get_running_loop()

        def _read() -> int:
            return int(
                self._contract.functions.getCumulativeFees(
                    Web3.to_checksum_address(translator)
                ).call()
            )

        raw = await loop.run_in_executor(None, _read)
        from ..onchain import units_to_usdc

        return units_to_usdc(raw)


async def record_fill(
    market_id: str,
    fill_amount_usdc: float,
    translator: str,
    *,
    onchain: Optional[OnChainClient] = None,
) -> str:
    client = onchain or OnChainClient()
    account = _operator_account()
    contract = client.w3.eth.contract(
        address=Web3.to_checksum_address(_router_address()),
        abi=_load_abi(),
    )
    amount_units = usdc_to_units(max(0.0, fill_amount_usdc))

    def _send() -> str:
        base = client._build_base_txn(account)
        txn = contract.functions.recordFill(
            market_id, amount_units, Web3.to_checksum_address(translator)
        ).build_transaction({**base, "gas": 250_000})
        return client._send(txn, account)

    tx_hash = await send_with_nonce_lock(account, _send)
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    logger.info(
        "recordFill(market=%s, amount=%.4f, translator=%s) tx=%s",
        market_id,
        fill_amount_usdc,
        translator,
        tx_hash,
    )
    return tx_hash


# ---------------------------------------------------------------------------
# Fee splitting (Path A — protocol-level 90/10 split without contract redeploy)
# ---------------------------------------------------------------------------

# Operator share of the on-chain builder fee. Anti-Sybil + protocol-sustainability
# constants are documented in outputs/WEB3_STORY.md (section 3, "Auto fee-splitting").
WINNER_SHARE: float = 0.90
TREASURY_SHARE: float = 0.10

# Minimum credit per recordFill leg, in USDC. The contract reverts on
# ``fillAmount == 0`` so we floor every leg to one base unit (1e-6 USDC).
_MIN_FILL_USDC: float = 1.0 / (10 ** 6)


async def record_fill_with_split(
    market_id: str,
    fill_amount_usdc: float,
    winner: str,
    treasury: str,
    *,
    onchain: Optional[OnChainClient] = None,
    winner_share: float = WINNER_SHARE,
) -> dict[str, Any]:
    """Split a Polymarket builder fee 90/10 between winner and treasury.

    This is Path A of the WEB3_STORY decentralization plan: rather than
    redeploying ``BuilderFeeRouter`` with split logic baked in, we emit two
    ``recordFill`` transactions from the orchestrator. Both legs are real
    on-chain transfers on Arc; nothing is custodial.

    Returns a dict with both tx hashes and per-leg USDC credit amounts::

        {
            "winner_tx":      "0x...",
            "treasury_tx":    "0x...",
            "winner_amount":  0.9,
            "treasury_amount": 0.1,
            "winner":         "0x...",
            "treasury":       "0x...",
        }

    Either tx hash may be ``None`` if that leg failed; the caller decides
    whether to mark the whole event simulated. We never fabricate a hash.
    """

    if fill_amount_usdc <= 0:
        raise ValueError("fill_amount_usdc must be positive for a 90/10 split")
    if not (0.0 < winner_share < 1.0):
        raise ValueError("winner_share must be in (0, 1)")

    winner_amount = max(_MIN_FILL_USDC, fill_amount_usdc * winner_share)
    treasury_amount = max(_MIN_FILL_USDC, fill_amount_usdc * (1.0 - winner_share))

    client = onchain or OnChainClient()

    winner_tx: Optional[str] = None
    treasury_tx: Optional[str] = None
    try:
        winner_tx = await record_fill(
            market_id, winner_amount, winner, onchain=client
        )
    except Exception as exc:  # pragma: no cover - best-effort, mirrors record_fill semantics
        logger.error(
            "record_fill_with_split: winner leg failed (market=%s winner=%s): %s",
            market_id,
            winner,
            exc,
        )
    try:
        treasury_tx = await record_fill(
            market_id, treasury_amount, treasury, onchain=client
        )
    except Exception as exc:  # pragma: no cover
        logger.error(
            "record_fill_with_split: treasury leg failed (market=%s treasury=%s): %s",
            market_id,
            treasury,
            exc,
        )

    logger.info(
        "record_fill_with_split(market=%s, total=%.6f, winner=%s [%.6f], "
        "treasury=%s [%.6f]) winner_tx=%s treasury_tx=%s",
        market_id,
        fill_amount_usdc,
        winner,
        winner_amount,
        treasury,
        treasury_amount,
        winner_tx,
        treasury_tx,
    )
    return {
        "winner_tx": winner_tx,
        "treasury_tx": treasury_tx,
        "winner_amount": winner_amount,
        "treasury_amount": treasury_amount,
        "winner": winner,
        "treasury": treasury,
    }


async def claim_fees(
    translator: str,
    *,
    onchain: Optional[OnChainClient] = None,
) -> str:
    client = onchain or OnChainClient()
    account = _operator_account()
    contract = client.w3.eth.contract(
        address=Web3.to_checksum_address(_router_address()),
        abi=_load_abi(),
    )

    def _send() -> str:
        base = client._build_base_txn(account)
        txn = contract.functions.claimFees(
            Web3.to_checksum_address(translator)
        ).build_transaction({**base, "gas": 200_000})
        return client._send(txn, account)

    tx_hash = await send_with_nonce_lock(account, _send)
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    logger.info("claimFees(translator=%s) tx=%s", translator, tx_hash)
    return tx_hash


__all__ = [
    "BuilderFeeRouter",
    "claim_fees",
    "record_fill",
    "record_fill_with_split",
    "WINNER_SHARE",
    "TREASURY_SHARE",
]
