"""Real ``QuestionRegistry`` adapter.

The on-chain contract function is
``registerQuestion(titleHash, sourceNewsHash, resolutionSource, cutoffTs,
                   category, winningTranslator)``.
The orchestrator passes us ``(event_id, candidate_hash, builder_code,
pipeline_trace_ipfs)``; we map those onto the contract args, supplying
sensible defaults for the fields that are not propagated through the
lifecycle yet (the resolution source defaults to the builder name, the
cutoff defaults to one month out, the category to ``"geopolitics"``).

Returns ``(question_id, tx_hash)`` so the orchestrator can persist both.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3

from ..onchain import OnChainClient, event_id_from_event

logger = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FOUNDRY_OUT = _REPO_ROOT / "contracts" / "out"

_QUESTION_REGISTRY_ABI_PATH = (
    _FOUNDRY_OUT / "QuestionRegistry.sol" / "QuestionRegistry.json"
)


def _operator_account() -> LocalAccount:
    pk = os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
    if not pk:
        raise RuntimeError(
            "HACKATHON_WALLET_PRIVATE_KEY not set; required for "
            "chain.question_registry operator writes"
        )
    return Account.from_key(pk)


def _load_abi() -> list[dict[str, Any]]:
    with _QUESTION_REGISTRY_ABI_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)["abi"]


def _registry_address() -> str:
    return os.environ.get(
        "QUESTION_REGISTRY_ADDRESS",
        "0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1",
    )


def _coerce_bytes32(value: Optional[str]) -> bytes:
    """Coerce arbitrary hex/string into a 32-byte digest."""

    if not value:
        return b"\x00" * 32
    raw = value[2:] if value.startswith("0x") else value
    try:
        as_bytes = bytes.fromhex(raw)
    except ValueError:
        return event_id_from_event(value)
    if len(as_bytes) == 32:
        return as_bytes
    if len(as_bytes) < 32:
        return as_bytes.rjust(32, b"\x00")
    return as_bytes[:32]


def _default_cutoff_ts() -> int:
    """Default 30 days out, in seconds since epoch."""

    return int(time.time()) + 30 * 24 * 3600


class QuestionRegistry:
    """Object-style facade for ``QuestionRegistry``."""

    def __init__(self, *, onchain: Optional[OnChainClient] = None) -> None:
        client = onchain or OnChainClient()
        self._onchain = client
        self._w3 = client.w3
        self._contract = client.w3.eth.contract(
            address=Web3.to_checksum_address(_registry_address()),
            abi=_load_abi(),
        )

    @property
    def contract(self):  # type: ignore[no-untyped-def]
        return self._contract

    async def commit_question(
        self,
        event_id: Any,
        candidate_hash: str,
        builder_code: str,
        pipeline_trace_ipfs: Optional[str],
        *,
        resolution_source: str = "polyglot-alpha",
        category: str = "geopolitics",
        cutoff_ts: Optional[int] = None,
    ) -> tuple[str, str]:
        return await commit_question(
            event_id,
            candidate_hash,
            builder_code,
            pipeline_trace_ipfs,
            onchain=self._onchain,
            resolution_source=resolution_source,
            category=category,
            cutoff_ts=cutoff_ts,
        )


async def commit_question(
    event_id: Any,
    candidate_hash: str,
    builder_code: str,
    pipeline_trace_ipfs: Optional[str],
    *,
    onchain: Optional[OnChainClient] = None,
    resolution_source: str = "polyglot-alpha",
    category: str = "geopolitics",
    cutoff_ts: Optional[int] = None,
) -> tuple[str, str]:
    """Call ``QuestionRegistry.registerQuestion``.

    Returns ``(question_id_hex, tx_hash)`` where ``question_id_hex`` is
    the contract-emitted ``QuestionRegistered.id`` as a ``0x``-prefixed
    hex string.
    """

    client = onchain or OnChainClient()
    account = _operator_account()
    contract = client.w3.eth.contract(
        address=Web3.to_checksum_address(_registry_address()),
        abi=_load_abi(),
    )

    title_hash = _coerce_bytes32(candidate_hash)
    # ``sourceNewsHash`` is whatever the orchestrator stashed in the
    # pipeline trace pointer — keccak'd to bytes32.
    source_news_hash = _coerce_bytes32(pipeline_trace_ipfs)
    cutoff = cutoff_ts or _default_cutoff_ts()
    winning_translator = builder_code or "polyglot-alpha"

    loop = asyncio.get_running_loop()

    def _send() -> tuple[str, int]:
        base = client._build_base_txn(account)
        txn = contract.functions.registerQuestion(
            title_hash,
            source_news_hash,
            resolution_source,
            cutoff,
            category,
            winning_translator,
        ).build_transaction({**base, "gas": 350_000})
        tx_hash_local = client._send(txn, account)
        # Wait for receipt so we can extract the emitted question id.
        receipt = client.w3.eth.wait_for_transaction_receipt(
            tx_hash_local, timeout=60
        )
        logs = contract.events.QuestionRegistered().process_receipt(receipt)
        qid_local = int(logs[0]["args"]["id"]) if logs else 0
        return tx_hash_local, qid_local

    tx_hash, qid = await loop.run_in_executor(None, _send)
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    question_id_hex = "0x" + format(qid, "x").rjust(40, "0") if qid else (
        "0x" + tx_hash[2:42]
    )
    logger.info(
        "registerQuestion(event_id=%s) qid=%s tx=%s", event_id, question_id_hex, tx_hash
    )
    return question_id_hex, tx_hash


__all__ = ["QuestionRegistry", "commit_question"]
