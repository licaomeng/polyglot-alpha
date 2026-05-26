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


__all__ = ["BuilderFeeRouter", "claim_fees", "record_fill"]
