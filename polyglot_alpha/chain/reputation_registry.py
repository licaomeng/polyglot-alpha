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

from ..onchain import OnChainClient, reputation_to_float, usdc_to_units

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
    loop = asyncio.get_running_loop()

    def _send() -> str:
        base = client._build_base_txn(account)
        txn = fn(*args).build_transaction({**base, "gas": 200_000})
        return client._send(txn, account)

    tx_hash = await loop.run_in_executor(None, _send)
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


__all__ = [
    "ReputationRegistryClient",
    "get_reputation",
    "update_reputation",
]
