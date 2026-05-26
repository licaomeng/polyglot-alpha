"""Real ``JudgePanel`` adapter (W9-A).

W8 audit found that ``contracts/src/JudgePanel.sol`` has 6 external
functions but **zero** Python clients and **zero** orchestrator
integration: the 11-judge ensemble runs entirely off-chain and persists
only to SQLite, contradicting the README's "on-chain verifiable
consensus" claim.

This module closes that gap. The orchestrator calls
:func:`record_attestation` at the end of Phase 4 (after the 11 judges
have voted) to stamp ONE aggregate attestation on-chain — the γ-hybrid
strategy from the W9-A spec:

  * Chain stores: ``keccak256(canonical_json_of_11_verdicts)`` +
    overall_score (scaled to 0..1000 so the contract's ``uint256 score``
    parameter keeps 3-decimal precision without floats).
  * IPFS / DB stores: the full 11-judge dossier JSON. Anyone can
    re-hash the dossier, compare to the on-chain ``attestationHash``,
    and verify the verdict has not been tampered with.

The contract requires the ``judge`` argument to be a *registered* judge
(``isTranslationJudge[judge] || isStyleJudge[judge]``). The aggregate
strategy uses the operator wallet itself as the panel aggregator —
:func:`ensure_aggregator_registered` lazily registers + stakes the
operator on first use (one 2-USDC TX, never repeated).

All chain writes are signed by the operator wallet
(``HACKATHON_WALLET_PRIVATE_KEY``) and serialized through the per-wallet
``send_with_nonce_lock`` helper. Mock-mode callers get a synthetic
``0xsim_*`` hash via :func:`is_mock_mode` / :func:`sim_tx_hash`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3

from ..onchain import (
    OnChainClient,
    event_id_from_event,
    send_with_nonce_lock,
)
from .sim_helpers import is_mock_mode, sim_tx_hash

logger = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FOUNDRY_OUT = _REPO_ROOT / "contracts" / "out"

_JUDGE_PANEL_ABI_PATH = _FOUNDRY_OUT / "JudgePanel.sol" / "JudgePanel.json"

# Gas budget for the operator-pushed ``recordAttestation`` TX. The body
# is a single ``emit`` + one storage write (incrementing
# ``attestationCount``); ~60k is the measured baseline on Arc testnet,
# we book 120k to absorb sequencer variance.
DEFAULT_GAS_ATTEST: int = 120_000

# Gas budget for the one-shot ``registerTranslationJudge`` stake call.
DEFAULT_GAS_REGISTER: int = 200_000

# Gas budget for the USDC ``approve`` call that precedes registration.
DEFAULT_GAS_APPROVE: int = 80_000

# Score scaling factor — the contract takes ``uint256 score``; we
# multiply our 0..1 overall_score by 1000 so 3-decimal precision is
# preserved on-chain (0.876 → 876). The verifier divides by 1000.
SCORE_SCALE: int = 1000


def _operator_account() -> LocalAccount:
    pk = os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
    if not pk:
        raise RuntimeError(
            "HACKATHON_WALLET_PRIVATE_KEY not set; required for "
            "chain.judge_panel_client operator writes"
        )
    return Account.from_key(pk)


def _load_abi() -> list[dict[str, Any]]:
    with _JUDGE_PANEL_ABI_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)["abi"]


def _panel_address() -> str:
    return os.environ.get(
        "JUDGE_PANEL_ADDRESS",
        "0x1eE7BADc48b52B36e086adb4a98E00cbff4efd9a",
    )


def _coerce_bytes32(value: Optional[str]) -> bytes:
    """Coerce arbitrary hex/string into a 32-byte digest.

    Mirrors :func:`question_registry._coerce_bytes32` so the
    chain-client behaviour is consistent across modules.
    """

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


def canonical_dossier_json(judges_dossier: list[dict[str, Any]]) -> bytes:
    """Serialize ``judges_dossier`` deterministically for hashing.

    Sorted keys + no whitespace so re-running on the same input produces
    byte-identical output (and therefore byte-identical keccak256). The
    verifier script depends on this property.
    """

    return json.dumps(
        judges_dossier, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def attestation_hash_for_dossier(judges_dossier: list[dict[str, Any]]) -> bytes:
    """Return the 32-byte keccak256 attestation hash for the dossier.

    Uses ``Web3.keccak`` (not Python ``hashlib.sha256``) so the on-chain
    EVM ``keccak256`` opcode produces the same digest, which means a
    Solidity verifier could re-validate without needing to know the
    Python serialization format.
    """

    return bytes(Web3.keccak(canonical_dossier_json(judges_dossier)))


def scale_overall_score(overall_score: float) -> int:
    """Scale a 0..1 overall_score to the contract's integer ``score`` arg.

    Values above 1.0 are assumed to be already on the 0..100 MQM scale
    and converted accordingly. Negative values clamp to 0; the upper
    bound is left unclamped so a future 0..100 use can encode 100_000
    cleanly.
    """

    if overall_score is None:
        return 0
    score = float(overall_score)
    if score < 0:
        return 0
    if score <= 1.0:
        return int(round(score * SCORE_SCALE))
    # Caller passed a 0..100 score directly; preserve precision.
    return int(round(score * (SCORE_SCALE / 100.0)))


# ---------------------------------------------------------------------------
# Class facade
# ---------------------------------------------------------------------------


class JudgePanelClient:
    """Object-style facade around the ``JudgePanel`` contract.

    Construct once at orchestrator startup so the per-wallet nonce-lock
    sequencing in :func:`send_with_nonce_lock` collapses across all
    Phase 4 calls.
    """

    def __init__(self, *, onchain: Optional[OnChainClient] = None) -> None:
        client = onchain or OnChainClient()
        self._onchain = client
        self._contract = client.w3.eth.contract(
            address=Web3.to_checksum_address(_panel_address()),
            abi=_load_abi(),
        )
        # Cache the result of ``ensure_aggregator_registered`` so we
        # don't pay the ``isTranslationJudge`` RPC read every event.
        self._aggregator_registered: bool = False

    @property
    def contract(self):  # type: ignore[no-untyped-def]
        return self._contract

    @property
    def address(self) -> str:
        return self._contract.address

    async def get_judge_info(self, judge: str) -> dict[str, Any]:
        """Read ``getJudgeInfo`` for ``judge``. Returns a dict for callers."""

        loop = asyncio.get_running_loop()

        def _read() -> tuple[int, bool, bool, int]:
            return self._contract.functions.getJudgeInfo(
                Web3.to_checksum_address(judge)
            ).call()

        stake, translation, style, attestations = await loop.run_in_executor(
            None, _read
        )
        return {
            "stake": int(stake),
            "is_translation_judge": bool(translation),
            "is_style_judge": bool(style),
            "attestation_count": int(attestations),
        }

    async def is_registered_judge(self, judge: str) -> bool:
        """Return ``True`` if ``judge`` can receive ``recordAttestation``."""

        info = await self.get_judge_info(judge)
        return info["is_translation_judge"] or info["is_style_judge"]

    async def ensure_aggregator_registered(
        self, *, aggregator_pk: Optional[str] = None
    ) -> Optional[str]:
        """Register the operator wallet as a translation judge if absent.

        γ-strategy requires *some* registered judge address to use as
        the panel aggregator. We use the operator's own wallet so the
        same key that signs ``recordAttestation`` can pre-stake itself
        without a separate funded wallet. Idempotent — subsequent calls
        return ``None`` once the wallet is on-chain as a judge.

        Returns the registration tx hash on first registration, ``None``
        if already registered or if the call could not be made (chain
        unreachable, etc.).
        """

        if self._aggregator_registered:
            return None
        pk = aggregator_pk or os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
        if not pk:
            raise RuntimeError(
                "aggregator_pk / HACKATHON_WALLET_PRIVATE_KEY required"
            )
        account = Account.from_key(pk)
        try:
            if await self.is_registered_judge(account.address):
                self._aggregator_registered = True
                logger.info(
                    "JudgePanelClient: aggregator %s already registered",
                    account.address,
                )
                return None
        except Exception:  # pragma: no cover - chain best-effort
            logger.exception(
                "JudgePanelClient: getJudgeInfo failed; attempting registration"
            )

        client = self._onchain
        stake = int(
            self._contract.functions.TRANSLATION_JUDGE_STAKE().call()
        )

        def _send() -> str:
            # Approve USDC for the panel contract, then register.
            base = client._build_base_txn(account)
            approve_txn = client.usdc.functions.approve(
                self._contract.address, stake
            ).build_transaction({**base, "gas": DEFAULT_GAS_APPROVE})
            client._send(approve_txn, account)
            base = client._build_base_txn(account)
            register_txn = self._contract.functions.registerTranslationJudge().build_transaction(
                {**base, "gas": DEFAULT_GAS_REGISTER}
            )
            return client._send(register_txn, account)

        try:
            tx_hash = await send_with_nonce_lock(account, _send)
        except Exception as exc:  # pragma: no cover - chain best-effort
            logger.error(
                "JudgePanelClient: registerTranslationJudge failed: %s", exc
            )
            return None
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        self._aggregator_registered = True
        logger.info(
            "JudgePanelClient: registered aggregator=%s stake_units=%d tx=%s",
            account.address,
            stake,
            tx_hash,
        )
        return tx_hash

    async def record_attestation(
        self,
        event_id: Any,
        judge: str,
        score_scaled: int,
        attestation_hash: bytes,
    ) -> str:
        """Operator-signed ``recordAttestation``. Returns the tx hash.

        Mock-mode short-circuits to a synthetic ``0xsim_*`` hash so the
        UI's muted-text gate hides the explorer link (W5-A2 contract).

        Raises:
            ValueError: if ``attestation_hash`` is not exactly 32 bytes.
        """

        if not isinstance(attestation_hash, (bytes, bytearray)) or len(
            attestation_hash
        ) != 32:
            raise ValueError(
                "attestation_hash must be exactly 32 bytes (got "
                f"{type(attestation_hash).__name__} len="
                f"{len(attestation_hash) if hasattr(attestation_hash, '__len__') else '?'})"
            )

        if is_mock_mode():
            stub = sim_tx_hash()
            logger.info(
                "JudgePanelClient: mock-mode recordAttestation "
                "event_id=%s judge=%s score=%d tx=%s",
                event_id,
                judge,
                score_scaled,
                stub,
            )
            return stub

        client = self._onchain
        account = _operator_account()
        eid = _coerce_bytes32(
            event_id if isinstance(event_id, str) else str(event_id)
        )

        def _send() -> str:
            base = client._build_base_txn(account)
            txn = self._contract.functions.recordAttestation(
                eid,
                Web3.to_checksum_address(judge),
                int(score_scaled),
                bytes(attestation_hash),
            ).build_transaction({**base, "gas": DEFAULT_GAS_ATTEST})
            return client._send(txn, account)

        tx_hash = await send_with_nonce_lock(account, _send)
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        logger.info(
            "JudgePanelClient: recordAttestation event_id=%s judge=%s "
            "score=%d hash=%s tx=%s",
            event_id,
            judge,
            score_scaled,
            "0x" + bytes(attestation_hash).hex(),
            tx_hash,
        )
        return tx_hash


# ---------------------------------------------------------------------------
# Module-level helpers (orchestrator imports these)
# ---------------------------------------------------------------------------


async def record_aggregate_attestation(
    event_id: Any,
    overall_score: float,
    judges_dossier: list[dict[str, Any]],
    *,
    aggregator_address: Optional[str] = None,
    onchain: Optional[OnChainClient] = None,
    client: Optional[JudgePanelClient] = None,
    auto_register: bool = True,
) -> dict[str, Any]:
    """End-to-end γ-strategy attestation for a finalized event.

    Steps:
      1. Compute ``attestationHash = keccak256(canonical_json(dossier))``.
      2. Scale ``overall_score`` to the contract's integer ``score`` arg.
      3. If ``auto_register``, lazily register the aggregator wallet so
         the contract's ``not a registered judge`` revert never fires
         on the first event after deploy.
      4. Call ``recordAttestation`` from the operator wallet.

    Returns a dict surfaced by the orchestrator on the event row::

        {
            "tx_hash": "0x...",
            "attestation_hash": "0x<keccak256>",
            "score_scaled": 876,
            "aggregator_address": "0x...",
            "register_tx": "0x..." | None,
            "strategy": "gamma_aggregate",
        }

    Never raises in live mode — chain failures are logged and surfaced
    via ``tx_hash=None`` so the lifecycle can continue.
    """

    attestation_hash = attestation_hash_for_dossier(judges_dossier)
    score_scaled = scale_overall_score(overall_score)
    operator_addr = aggregator_address or _operator_account().address

    if is_mock_mode():
        stub_tx = sim_tx_hash()
        logger.info(
            "JudgePanelClient: mock-mode aggregate attestation "
            "event_id=%s score=%d hash=0x%s tx=%s",
            event_id,
            score_scaled,
            attestation_hash.hex(),
            stub_tx,
        )
        return {
            "tx_hash": stub_tx,
            "attestation_hash": "0x" + attestation_hash.hex(),
            "score_scaled": score_scaled,
            "aggregator_address": operator_addr,
            "register_tx": None,
            "strategy": "gamma_aggregate",
        }

    panel_client = client or JudgePanelClient(onchain=onchain)
    register_tx: Optional[str] = None
    if auto_register:
        try:
            register_tx = await panel_client.ensure_aggregator_registered()
        except Exception as exc:  # pragma: no cover - chain best-effort
            logger.error(
                "JudgePanelClient: ensure_aggregator_registered failed: %s",
                exc,
            )
    try:
        tx_hash: Optional[str] = await panel_client.record_attestation(
            event_id, operator_addr, score_scaled, attestation_hash
        )
    except Exception as exc:  # pragma: no cover - chain best-effort
        logger.error(
            "JudgePanelClient: recordAttestation failed event_id=%s: %s",
            event_id,
            exc,
        )
        tx_hash = None
    return {
        "tx_hash": tx_hash,
        "attestation_hash": "0x" + attestation_hash.hex(),
        "score_scaled": score_scaled,
        "aggregator_address": operator_addr,
        "register_tx": register_tx,
        "strategy": "gamma_aggregate",
    }


__all__ = [
    "DEFAULT_GAS_ATTEST",
    "DEFAULT_GAS_REGISTER",
    "JudgePanelClient",
    "SCORE_SCALE",
    "attestation_hash_for_dossier",
    "canonical_dossier_json",
    "record_aggregate_attestation",
    "scale_overall_score",
]
