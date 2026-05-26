"""``BaseTranslatorAgent``: shared lifecycle, pipeline, and chain plumbing.

Subclasses tune three knobs:

1. ``MODEL_ID`` - which LLM the pipeline runs against.
2. ``AGENT_NAME`` - short identifier used in logs and reputation reads.
3. ``bid_strategy`` - per-agent risk profile that turns an event into a USDC
   bid amount.

Everything else (running analysts -> translators -> synthesizer -> quality
evaluator, computing the candidate hash, submitting the bid on-chain,
listening for new ``AuctionOpened`` events) lives in this base class so
the agents can stay as thin overrides.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

from eth_account.signers.local import LocalAccount
from web3 import Web3

from .. import analysts, quality_eval, synthesizer, translators
from ..chain.sim_helpers import is_mock_mode, sim_tx_hash
from ..llm import LLMCallable, make_llm
from ..onchain import (
    OnChainClient,
    REGISTRATION_STAKE_USDC,
    event_id_from_event,
    send_with_nonce_lock,
    usdc_to_units,
)
from ..schemas import (
    EvaluationResult,
    NewsEvent,
    Question,
    event_dict_to_model,
)
from .internal_debate import InternalDebateResult, run_internal_debate

logger = logging.getLogger(__name__)


LLMFactory = Callable[[], LLMCallable]
EventDict = Dict[str, Any]


# Default bid window (USDC). Subclasses override via ``bid_strategy``.
_DEFAULT_BID_MIN_USDC = 0.30
_DEFAULT_BID_MAX_USDC = 1.20
_DEFAULT_REPUTATION = 1.0


@dataclass(frozen=True)
class BidSubmission:
    """Result of :meth:`BaseTranslatorAgent.submit_bid`."""

    event_id: str
    bid_amount_usdc: float
    bid_amount_units: int
    candidate_hash_hex: str
    tx_hash: str


# ---------------------------------------------------------------------------
# Public SDK surface types
# ---------------------------------------------------------------------------
#
# These TypedDicts describe the shapes external operators see when they
# implement an agent through ``polyglot_alpha.agent_sdk``. They are kept
# intentionally narrow: only fields the protocol cares about. Each operator
# is free to attach extra keys (operators that do attach extras MUST keep
# them deterministic so the on-chain candidate_hash stays reproducible).


class EventPayload(TypedDict, total=False):
    """Raw event payload published by the protocol's ingestion layer.

    Operators receive this dict and produce a :class:`CandidateQuestion`.
    Only ``event_id`` / ``title_zh`` / ``body_zh`` / ``cutoff_ts`` are
    guaranteed; additional metadata (``source``, ``topic``, etc.) is
    optional and may be absent on dev/test events.
    """

    event_id: str
    title_zh: str
    body_zh: str
    url: str
    cutoff_ts: int
    topic: str
    source: str


class CandidateQuestion(TypedDict, total=False):
    """The market-question shape operators are expected to emit.

    The required keys (``question_en``, ``resolution_criteria``,
    ``end_date_iso``) match what the on-chain :class:`Question` carries.
    Operators may attach ``tags``, ``resolution_source``, ``title``,
    ``category``, ``meta``, etc. as long as the resulting dict serialises
    deterministically (used to compute candidate_hash).
    """

    question_en: str
    resolution_criteria: str
    end_date_iso: str
    tags: List[str]
    resolution_source: str
    title: str
    category: str
    meta: Dict[str, Any]


class BidIntent(TypedDict, total=False):
    """Pre-submission descriptor of the bid an operator intends to place.

    Operators construct one of these locally and then call
    :meth:`BaseTranslatorAgent.submit_bid` (or the equivalent chain
    helper) to broadcast it. The ``candidate_hash_hex`` MUST be the
    SHA-256 over the canonicalised ``candidate`` payload.
    """

    event_id: str
    bid_amount_usdc: float
    candidate_hash_hex: str
    candidate: CandidateQuestion


class BaseTranslatorAgent:
    """Bid-and-run translator. Subclasses set ``MODEL_ID`` / ``AGENT_NAME``."""

    MODEL_ID: str = "gemini-2.0-flash"
    AGENT_NAME: str = "base"

    # Bid window in USDC. Subclasses may override either constant.
    BID_MIN_USDC: float = _DEFAULT_BID_MIN_USDC
    BID_MAX_USDC: float = _DEFAULT_BID_MAX_USDC

    def __init__(
        self,
        wallet_pk: str,
        *,
        llm_factory: Optional[LLMFactory] = None,
        reputation_history: Optional[float] = None,
        onchain: Optional[OnChainClient] = None,
    ) -> None:
        if not wallet_pk:
            raise ValueError("wallet_pk is required (set <AGENT>_WALLET_PRIVATE_KEY env)")
        self._llm_factory: LLMFactory = llm_factory or (lambda: make_llm(self.MODEL_ID))
        self.reputation_history: float = (
            reputation_history if reputation_history is not None else _DEFAULT_REPUTATION
        )
        self._onchain = onchain  # lazily constructed if None
        self.account: LocalAccount = OnChainClient.account_from_pk(wallet_pk)

    # ------------------------------------------------------------------
    # Lazy / shared resources
    # ------------------------------------------------------------------

    @property
    def onchain(self) -> OnChainClient:
        if self._onchain is None:
            self._onchain = OnChainClient()
        return self._onchain

    @property
    def address(self) -> str:
        return self.account.address

    # ------------------------------------------------------------------
    # Pre-bid evaluation
    # ------------------------------------------------------------------

    async def evaluate_event(self, event_dict: EventDict) -> EvaluationResult:
        """Heuristic self-evaluation used to size the bid.

        Subclasses can override; default uses event-body length as a rough
        proxy for confidence and ``bid_strategy`` for the final amount.
        """

        event = event_dict_to_model(event_dict)
        body_len = len(event.body_zh)
        # Saturates quickly: 0.5 baseline + up to 0.5 from body length.
        confidence = min(1.0, 0.5 + body_len / 4000.0)
        estimated_quality = min(1.0, 0.6 + body_len / 6000.0)
        expected_cost_usdc = 0.05 + body_len / 8000.0  # naive token-cost proxy
        bid_amount = self.bid_strategy(event_dict)
        return EvaluationResult(
            confidence=confidence,
            expected_cost_usdc=expected_cost_usdc,
            estimated_quality=estimated_quality,
            bid_amount_usdc=bid_amount,
        )

    # ------------------------------------------------------------------
    # Bid strategy (per-agent override)
    # ------------------------------------------------------------------

    def bid_strategy(self, event: EventDict) -> float:
        """Return a USDC bid amount inside ``[BID_MIN_USDC, BID_MAX_USDC]``.

        Default policy: midpoint of the agent's window. Subclasses override.
        """

        return round((self.BID_MIN_USDC + self.BID_MAX_USDC) / 2.0, 4)

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    async def run_pipeline(self, event_dict: EventDict) -> Question:
        """Run analysts -> translators -> synthesizer -> quality_eval."""

        event = event_dict_to_model(event_dict)
        llm = self._llm_factory()
        reports = await analysts.run_analysts(event, llm)
        candidates = await translators.propose_candidates(event, reports, llm)
        question = synthesizer.synthesize(event, candidates)
        score = quality_eval.score_question(question)
        question.confidence = score.score
        question.quality_score = score.score
        return question

    # ------------------------------------------------------------------
    # Reference seeder's internal debate entry point
    # ------------------------------------------------------------------

    async def _propose_n_candidates(
        self, event_dict: EventDict
    ) -> List[Dict[str, Any]]:
        """Produce N candidate dicts for the debate loop.

        Wraps :func:`translators.propose_candidates` so the debate loop
        sees plain dicts (the public SDK contract) rather than the
        internal :class:`TranslationCandidate` Pydantic model. Tagging
        each candidate with ``meta.model`` here means the refine stage
        can route back to the same LLM that authored the winner.
        """

        event = event_dict_to_model(event_dict)
        llm = self._llm_factory()
        reports = await analysts.run_analysts(event, llm)
        candidates = await translators.propose_candidates(
            event, reports, llm, model_id=self.MODEL_ID
        )
        return [c.model_dump() for c in candidates]

    async def propose_candidate(
        self, event_dict: EventDict
    ) -> InternalDebateResult:
        """Produce a single candidate for the bid via the internal debate.

        This is the reference seeder's implementation: it runs the
        translator stage to get N candidates, then the critic +
        moderator + refine stages to produce a single polished
        candidate. The returned :class:`InternalDebateResult` carries
        both the final candidate and the full intermediate trace, so
        the caller can compute candidate_hash over ``final_candidate``
        and still emit the trace for IPFS / auditing.

        External operators do **NOT** have to use this method. The
        protocol only requires that the on-chain bid carries a
        candidate_hash matching the candidate eventually committed. How
        the candidate is produced is each operator's choice.
        """

        return await run_internal_debate(
            event_dict, propose_candidates_fn=self._propose_n_candidates
        )

    # ------------------------------------------------------------------
    # Chain interactions
    # ------------------------------------------------------------------

    async def ensure_registered(self) -> Optional[str]:
        """Register the agent on-chain (one-time) if not already registered.

        Returns the tx hash if a registration was sent, else ``None``.
        Approval for the required stake is handled automatically.
        """

        loop = asyncio.get_running_loop()
        if await loop.run_in_executor(None, self.onchain.is_registered, self.address):
            return None
        stake_units = usdc_to_units(REGISTRATION_STAKE_USDC)
        # Serialize the approve+register pair under one lock so both
        # signed-txs use sequential nonces from the agent's wallet.
        def _approve_and_register() -> str:
            self.onchain.approve_usdc(self.account, stake_units)
            return self.onchain.register_agent(self.account)

        return await send_with_nonce_lock(self.account, _approve_and_register)

    async def submit_bid(
        self,
        event_id: str,
        bid_amount: float,
        candidate_metadata_hash: bytes,
    ) -> str:
        """Submit a bid to the on-chain auction. Returns the tx hash.

        W5-A2: in ``mode='mock'`` lifecycles we MUST NOT issue an RPC.
        Return a synthetic ``0xsim_*`` hash immediately so the caller can
        persist a non-null tx_hash without burning gas. The UI's arcscan
        gate keys off the ``0xsim_`` prefix and renders muted text.
        """

        if bid_amount <= 0:
            raise ValueError("bid_amount must be > 0")
        if len(candidate_metadata_hash) != 32:
            raise ValueError("candidate_metadata_hash must be 32 bytes")
        if is_mock_mode():
            logger.info(
                "agent=%s submit_bid skipped (mock mode); returning sim hash",
                self.AGENT_NAME,
            )
            return sim_tx_hash()
        event_id_b = event_id_from_event(event_id)
        bid_units = usdc_to_units(bid_amount)
        return await send_with_nonce_lock(
            self.account,
            lambda: self.onchain.submit_bid(
                self.account,
                event_id_b,
                bid_units,
                candidate_metadata_hash,
            ),
        )

    @staticmethod
    def hash_question(question: Question) -> bytes:
        """Deterministic 32-byte hash of the synthesized question, suitable
        for the ``candidateHash`` parameter of ``submitBid``."""

        payload = question.model_dump(mode="json")
        # Sort keys so the hash is independent of dict insertion order.
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).digest()

    @staticmethod
    def hash_candidate_dict(candidate: Dict[str, Any]) -> bytes:
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).digest()

    # ------------------------------------------------------------------
    # Event listener
    # ------------------------------------------------------------------

    async def listen_for_events(
        self,
        *,
        poll_interval_s: float = 2.0,
        from_block: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
        max_events: Optional[int] = None,
    ) -> None:
        """Subscribe to ``AuctionOpened`` and react to each one.

        Implemented as a polling loop (web3.py 7.x doesn't ship a usable
        websocket subscription helper for arbitrary EVM chains). Each
        iteration:

        1. Fetches new ``AuctionOpened`` logs since ``from_block``.
        2. Asks ``evaluate_event`` whether to bid.
        3. Optionally runs the pipeline + submits the bid.

        Set ``stop_event`` to terminate the loop cleanly, or ``max_events``
        for tests / dry runs.
        """

        client = self.onchain
        loop = asyncio.get_running_loop()
        if from_block is None:
            from_block = await loop.run_in_executor(
                None, lambda: client.w3.eth.block_number
            )
        seen = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                logger.info("agent=%s stop_event set, exiting listener", self.AGENT_NAME)
                return
            try:
                latest = await loop.run_in_executor(
                    None, lambda: client.w3.eth.block_number
                )
                if latest >= from_block:
                    event_filter = client.auction.events.AuctionOpened.create_filter(
                        from_block=from_block, to_block=latest
                    )
                    entries = await loop.run_in_executor(
                        None, event_filter.get_all_entries
                    )
                    for entry in entries:
                        await self._handle_auction_opened(entry)
                        seen += 1
                        if max_events is not None and seen >= max_events:
                            return
                    from_block = latest + 1
            except Exception:  # pragma: no cover - depends on live RPC
                logger.exception("agent=%s poll iteration failed", self.AGENT_NAME)
            await asyncio.sleep(poll_interval_s)

    async def _handle_auction_opened(self, log_entry: Any) -> None:
        """Default reaction: evaluate, run internal debate, submit a bid.

        The four reference seeders use ``propose_candidate`` (which
        wraps the internal debate loop), so the candidate_hash they
        commit on-chain is computed over the POST-REFINE final
        candidate. That guarantees the provenance chain (raw event ->
        2 candidates -> critique -> moderator -> refine -> bid) is
        verifiable from the on-chain hash alone.
        """

        args = getattr(log_entry, "args", None) or log_entry["args"]
        event_id_bytes = bytes(args["eventId"])
        event_id_hex = "0x" + event_id_bytes.hex()
        event_dict: EventDict = {
            "event_id": event_id_hex,
            "title_zh": "",
            "body_zh": "",
            "url": "",
            "cutoff_ts": 0,
        }
        evaluation = await self.evaluate_event(event_dict)
        if evaluation.bid_amount_usdc <= 0:
            logger.info(
                "agent=%s skipping event=%s (zero bid)", self.AGENT_NAME, event_id_hex
            )
            return
        debate = await self.propose_candidate(event_dict)
        candidate_hash = self.hash_candidate_dict(debate.final_candidate)
        tx_hash = await self.submit_bid(
            event_id_hex, evaluation.bid_amount_usdc, candidate_hash
        )
        logger.info(
            "agent=%s bid event=%s amount=%.4f tx=%s debate_ms=%d",
            self.AGENT_NAME,
            event_id_hex,
            evaluation.bid_amount_usdc,
            tx_hash,
            debate.total_duration_ms,
        )

    # ------------------------------------------------------------------
    # Utilities for tests
    # ------------------------------------------------------------------

    @classmethod
    def env_private_key_name(cls) -> str:
        return f"{cls.AGENT_NAME.upper()}_WALLET_PRIVATE_KEY"

    @classmethod
    def from_env(cls, **kwargs: Any) -> "BaseTranslatorAgent":
        """Build an agent from ``<AGENT>_WALLET_PRIVATE_KEY`` env var."""

        pk = os.environ.get(cls.env_private_key_name())
        if not pk:
            raise RuntimeError(
                f"missing env var {cls.env_private_key_name()} for {cls.AGENT_NAME}"
            )
        return cls(wallet_pk=pk, **kwargs)
