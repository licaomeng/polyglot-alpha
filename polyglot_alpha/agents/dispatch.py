"""Pipeline dispatch — glue from orchestrator to the 5-layer translation
pipeline + 4-agent evaluate/bid stage.

This module is what ``orchestrator.py`` imports as ``from .agents import dispatch``.
When the import (or the underlying chain on-chain stack) fails, the orchestrator
falls back to a static template emitter that always outputs
``"Will X by December 31, 2026?"``. To keep the orchestrator's real pipeline
alive we therefore:

* Make sure the module imports cleanly even when the LLM keys are missing
  (``make_llm`` already returns a :class:`MockLLM` in that case).
* Provide both the orchestrator-facing surface (``run_for_winner`` +
  :class:`PipelineResult`) **and** the standalone surface the rest of the
  product uses (``collect_bids_inline`` + a ``polymarket.types.Question``
  shaped ``run_pipeline``).
* Tolerate per-agent LLM failures during ``collect_bids_inline`` — one
  bad agent must not kill the whole auction.

The public surface is enumerated in :data:`__all__` at the bottom.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from eth_account import Account

from .. import analysts, quality_eval, synthesizer, translators
from ..llm import LLMCallable, make_llm
from ..schemas import Question as SchemaQuestion, event_dict_to_model
from . import AGENT_REGISTRY

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WALLETS_PATH = _REPO_ROOT / "outputs" / "agent_wallets.json"

# Default bid window in USDC used when an agent's ``evaluate_event`` blows up
# but we still want to register a non-zero bid in the auction window.
_DEFAULT_FALLBACK_BID_USDC: float = 1.0
_DEFAULT_FALLBACK_REPUTATION: float = 1.0
_DEFAULT_AUCTION_WINDOW_SECONDS: float = 30.0
_DEFAULT_PIPELINE_TIMEOUT_SECONDS: float = 120.0


# ---------------------------------------------------------------------------
# PipelineResult shape (mirrors orchestrator.PipelineResult by-field).
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """What :func:`run_for_winner` returns to the orchestrator."""

    final_question: dict[str, Any]
    pipeline_trace_ipfs: Optional[str]
    candidate_hash: str


# ---------------------------------------------------------------------------
# Agent-name resolution
# ---------------------------------------------------------------------------


def _load_wallet_map() -> dict[str, str]:
    """Return ``{lowercased_address: agent_name}`` from outputs/agent_wallets.json."""

    if not _WALLETS_PATH.exists():
        return {}
    try:
        data = json.loads(_WALLETS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    for name, info in data.items():
        addr = info.get("address") if isinstance(info, dict) else None
        if addr:
            out[str(addr).lower()] = name
    return out


def resolve_agent_name(agent_address: str) -> str:
    """Map a winner address back to ``gemini`` / ``deepseek`` / ``qwen`` / ``llama``.

    Falls back to ``"gemini"`` so the pipeline always has a valid model
    binding even if the on-chain winner is an unregistered demo address.
    """

    if not agent_address:
        return "gemini"
    wallets = _load_wallet_map()
    return wallets.get(agent_address.lower(), "gemini")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_event(event_dict: dict[str, Any]) -> dict[str, Any]:
    """Pad missing fields so :func:`event_dict_to_model` does not blow up."""

    title = (
        event_dict.get("title_zh")
        or event_dict.get("title")
        or "PolyglotAlpha demo event"
    )
    body = (
        event_dict.get("body_zh")
        or event_dict.get("body")
        or event_dict.get("summary")
        or title
    )
    cutoff_ts = int(event_dict.get("cutoff_ts") or 0)
    if not cutoff_ts:
        cutoff_ts = int(datetime.now(timezone.utc).timestamp()) + 30 * 24 * 3600
    return {
        **event_dict,
        "event_id": str(
            event_dict.get("event_id") or event_dict.get("eventId") or ""
        ),
        "title_zh": title,
        "body_zh": body,
        "url": event_dict.get("url") or "",
        "cutoff_ts": cutoff_ts,
    }


def _throwaway_pk() -> str:
    """Generate a fresh, never-funded private key for eval-only agents.

    :class:`BaseTranslatorAgent` requires a non-empty ``wallet_pk`` so it can
    derive an address. For the in-process auction we just want to size a bid;
    we never broadcast a transaction with this key.
    """

    return Account.create().key.hex()


def _candidate_hash_for_agent(
    agent_name: str, evaluation: Any, event_dict: dict[str, Any]
) -> str:
    """Deterministic per-agent candidate hash for the bid payload."""

    body = {
        "agent": agent_name,
        "bid_amount_usdc": getattr(evaluation, "bid_amount_usdc", None),
        "confidence": getattr(evaluation, "confidence", None),
        "title": event_dict.get("title") or event_dict.get("title_zh"),
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _polymarket_question_from_schema(
    event_dict: dict[str, Any],
    question: SchemaQuestion,
) -> Any:
    """Lift a ``schemas.Question`` into a ``polymarket.types.Question``.

    Imported lazily so a missing ``polymarket`` package does not break the
    orchestrator's import of this module.
    """

    from ..polymarket.types import Question as PolymarketQuestion  # local import

    qid = str(
        event_dict.get("event_id")
        or event_dict.get("question_id")
        or question.event_id
        or uuid.uuid4().hex
    )
    return PolymarketQuestion(
        question_id=qid,
        text=question.question_en,
        category=event_dict.get("category", "geopolitics"),
        resolution_source=event_dict.get("resolution_source") or "operator",
        end_date_iso=question.end_date_iso,
    )


def _build_final_question_dict(
    event_dict: dict[str, Any],
    question: SchemaQuestion,
) -> dict[str, Any]:
    """Project the agent's :class:`Question` into the orchestrator's wire shape."""

    title = (question.question_en or "").strip()
    cutoff_dt = datetime.now(timezone.utc).replace(month=12, day=31, microsecond=0)
    cutoff_human = cutoff_dt.strftime("%B %d, %Y")
    # Only prepend the "Will ... by <date>?" template if the candidate does
    # NOT already start with "Will" (case-insensitive). Otherwise we end up
    # with "Will Will the People's Bank ..." doubled prefixes when the LLM
    # already produced a P1-shape title.
    if not title.lower().startswith("will "):
        title = f"Will {title.rstrip('?')} by {cutoff_human}?"
    return {
        "title": title,
        "description": event_dict.get("summary") or event_dict.get("title") or title,
        "resolution_criteria": question.resolution_criteria,
        "resolution_source": event_dict.get("resolution_source") or "operator",
        "cutoff_ts": question.end_date_iso or cutoff_dt.isoformat(),
        "end_date_iso": question.end_date_iso,
        "category": event_dict.get("category", "geopolitics"),
        "source_news": event_dict.get("title") or event_dict.get("title_zh") or "",
        "source_language": event_dict.get("language", "zh"),
        "target_language": "en",
        "outcomes": ["Yes", "No"],
        "confidence": question.confidence,
        "quality_score": question.quality_score,
    }


# ---------------------------------------------------------------------------
# Pipeline driver
# ---------------------------------------------------------------------------


async def _run_pipeline_schema(
    event_dict: dict[str, Any],
    agent_name: str,
    llm_factory: Optional[Callable[[], LLMCallable]] = None,
) -> SchemaQuestion:
    """Run analysts -> translators -> synthesizer -> quality_eval.

    Returns the internal :class:`schemas.Question` (with confidence and
    quality_score populated). All four stages execute as in
    :meth:`BaseTranslatorAgent.run_pipeline` but with an explicit
    ``llm_factory`` injection point for tests.
    """

    cls = AGENT_REGISTRY.get(agent_name) or AGENT_REGISTRY["gemini"]
    model_id = cls.MODEL_ID
    factory = llm_factory or (lambda: make_llm(model_id))
    llm = factory()

    coerced = _coerce_event(event_dict)
    event = event_dict_to_model(coerced)

    # Layer 1: source analysts (parallel).
    reports = await analysts.run_analysts(event, llm)
    # Layer 2: translator debate (parallel candidates).
    candidates = await translators.propose_candidates(event, reports, llm)
    # Layer 3: synthesizer (pick / merge).
    question = synthesizer.synthesize(event, candidates)
    # Layer 4: quality eval (internal sanity check, NOT the 11-judge panel).
    score = quality_eval.score_question(question)
    question.confidence = score.score
    question.quality_score = score.score
    return question


async def run_pipeline(
    event: dict[str, Any],
    *,
    winner_agent_name: str,
    llm_factory: Optional[Callable[[], LLMCallable]] = None,
) -> Any:
    """Execute the 5-layer translation pipeline using the winning agent.

    Layer 1: Source Analysts (``analysts.run_analysts``)
    Layer 2: Translator Debate (``translators.propose_candidates``)
    Layer 3: Synthesizer (``synthesizer.synthesize``)
    Layer 4: Quality Evaluation (``quality_eval.score_question`` — internal
              sanity check, NOT the 11-judge panel)
    Layer 5: Final ``polymarket.types.Question`` construction (this module)

    Returns a ``polymarket.types.Question`` Pydantic model ready for
    submission. The full layer trace is attached as the model's
    ``layer_trace`` extra attribute so callers (UI / API) can inspect each
    stage.
    """

    question = await _run_pipeline_schema(
        event, winner_agent_name, llm_factory=llm_factory
    )
    pm_question = _polymarket_question_from_schema(event, question)

    # Stash a layer trace for UI rendering. ``polymarket.types.Question`` is a
    # Pydantic v2 model with default config; setting an attribute is enough
    # for our consumers, but we also expose it via ``model_extra`` if the
    # model allows extras.
    layer_trace = {
        "analyst_reports": [
            r.model_dump() if hasattr(r, "model_dump") else dict(r)
            for r in getattr(question, "_analyst_reports", []) or []
        ],
        "synthesized": question.model_dump(),
        "quality_score": question.quality_score,
        "confidence": question.confidence,
        "winner_agent": winner_agent_name,
    }
    try:
        object.__setattr__(pm_question, "layer_trace", layer_trace)
    except (AttributeError, ValueError):  # pragma: no cover - Pydantic strict
        pass
    return pm_question


# ---------------------------------------------------------------------------
# Bid collection (4 agents in parallel)
# ---------------------------------------------------------------------------


async def _safe_agent_bid(
    agent_name: str,
    agent_cls: type,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Drive one agent's pre-bid evaluation. Always returns a dict.

    On LLM / construction failure we still emit a safe default so the
    auction window is never starved of bids.
    """

    coerced = _coerce_event(event_dict)
    fallback_address = f"0x{agent_name.lower()}_eval_only"

    try:
        agent = agent_cls(wallet_pk=_throwaway_pk())
    except Exception as exc:  # pragma: no cover - construction is trivial
        logger.warning(
            "dispatch.collect_bids_inline: agent=%s construction failed: %s",
            agent_name,
            exc,
        )
        return {
            "agent_address": fallback_address,
            "agent_name": agent_name,
            "bid_amount": _DEFAULT_FALLBACK_BID_USDC,
            "candidate_hash": "0x0",
            "reputation": _DEFAULT_FALLBACK_REPUTATION,
            "confidence": 0.5,
            "expected_cost_usdc": 0.0,
            "llm_model": getattr(agent_cls, "MODEL_ID", "unknown"),
            "_error": f"construction:{exc}",
        }

    try:
        evaluation = await agent.evaluate_event(coerced)
    except Exception as exc:  # broad: LLM quota, parse error, network
        logger.warning(
            "dispatch.collect_bids_inline: agent=%s evaluate_event failed: %s",
            agent_name,
            exc,
        )
        return {
            "agent_address": agent.address,
            "agent_name": agent_name,
            "bid_amount": _DEFAULT_FALLBACK_BID_USDC,
            "candidate_hash": "0x0",
            "reputation": _DEFAULT_FALLBACK_REPUTATION,
            "confidence": 0.5,
            "expected_cost_usdc": 0.0,
            "llm_model": agent_cls.MODEL_ID,
            "_error": f"evaluate:{exc}",
        }

    return {
        "agent_address": agent.address,
        "agent_name": agent_name,
        "bid_amount": float(evaluation.bid_amount_usdc),
        "candidate_hash": _candidate_hash_for_agent(
            agent_name, evaluation, coerced
        ),
        "reputation": float(getattr(evaluation, "estimated_quality", 1.0)),
        "confidence": float(evaluation.confidence),
        "expected_cost_usdc": float(evaluation.expected_cost_usdc),
        "llm_model": agent_cls.MODEL_ID,
    }


async def collect_bids_inline(
    event: dict[str, Any],
    *,
    window_seconds: float = _DEFAULT_AUCTION_WINDOW_SECONDS,
) -> list[dict[str, Any]]:
    """Run all 4 reference agents in parallel, each emits a (mock) bid.

    No real on-chain transactions are submitted — that is the auction
    client's job. This function exists so the in-process demo path can
    surface bid-sized differences (each agent's :meth:`bid_strategy` skews
    the amount) without requiring a live RPC.

    Each returned dict has at least::

        {
            "agent_address": str,
            "agent_name":    str,
            "bid_amount":    float,            # USDC
            "candidate_hash": str,
            "reputation":    float,            # 0-1
            "confidence":    float,            # 0-1
            "expected_cost_usdc": float,
            "llm_model":     str,
        }

    A failed agent yields the same shape with an extra ``_error`` key so
    callers can render the failure inline.
    """

    items = list(AGENT_REGISTRY.items())  # [(name, cls), ...] — 4 entries.
    tasks = [
        asyncio.create_task(_safe_agent_bid(name, cls, event))
        for name, cls in items
    ]
    bids: list[dict[str, Any]] = []
    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=max(window_seconds, 0.0),
            return_when=asyncio.ALL_COMPLETED,
        )
        for task in done:
            try:
                bids.append(task.result())
            except Exception as exc:  # task itself raised — log + skip
                logger.warning(
                    "dispatch.collect_bids_inline: agent task crashed: %s",
                    exc,
                )
        for task in pending:
            task.cancel()
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        raise
    return bids


# ---------------------------------------------------------------------------
# Orchestrator-facing entry point
# ---------------------------------------------------------------------------


async def run_for_winner(
    event_dict: dict[str, Any],
    winner_address: str,
) -> PipelineResult:
    """Orchestrator-facing entry point.

    Resolves the agent class from ``winner_address`` and drives the full
    5-layer pipeline. On any LLM failure we degrade to a deterministic
    placeholder so the lifecycle still emits a hashable candidate.
    """

    agent_name = resolve_agent_name(winner_address)
    logger.info(
        "dispatch.run_for_winner: address=%s -> agent=%s",
        winner_address,
        agent_name,
    )

    try:
        question = await asyncio.wait_for(
            _run_pipeline_schema(event_dict, agent_name),
            timeout=_DEFAULT_PIPELINE_TIMEOUT_SECONDS,
        )
        final_question = _build_final_question_dict(event_dict, question)
    except (
        asyncio.TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        httpx.HTTPError,
    ) as exc:
        logger.warning(
            "dispatch.run_for_winner: pipeline failed (%s); emitting fallback",
            exc,
        )
        title_raw = (
            event_dict.get("title")
            or event_dict.get("title_zh")
            or "PolyglotAlpha demo question"
        ).strip()
        cutoff = datetime.now(timezone.utc).replace(month=12, day=31, microsecond=0)
        cutoff_iso = cutoff.isoformat()
        # Avoid doubling the "Will" prefix when the upstream title already
        # starts with it (matches _build_final_question_dict behaviour).
        if title_raw.lower().startswith("will "):
            fallback_title = title_raw if title_raw.endswith("?") else f"{title_raw}?"
        else:
            fallback_title = (
                f"Will {title_raw.rstrip('?')} by {cutoff.strftime('%B %d, %Y')}?"
            )
        final_question = {
            "title": fallback_title,
            "description": event_dict.get("summary") or title_raw,
            "resolution_criteria": (
                "Resolves YES if the underlying event is confirmed by an "
                "authoritative report on or before the cutoff, otherwise NO."
            ),
            "resolution_source": "operator",
            "cutoff_ts": cutoff_iso,
            "end_date_iso": cutoff_iso,
            "category": event_dict.get("category", "geopolitics"),
            "source_news": title_raw,
            "source_language": event_dict.get("language", "zh"),
            "target_language": "en",
            "outcomes": ["Yes", "No"],
            "confidence": 0.5,
            "quality_score": 0.5,
        }

    candidate_hash = hashlib.sha256(
        json.dumps(final_question, sort_keys=True).encode()
    ).hexdigest()

    return PipelineResult(
        final_question=final_question,
        pipeline_trace_ipfs=f"ipfs://pipeline/{agent_name}/{candidate_hash[:12]}",
        candidate_hash=candidate_hash,
    )


__all__ = [
    "PipelineResult",
    "collect_bids_inline",
    "resolve_agent_name",
    "run_for_winner",
    "run_pipeline",
]
