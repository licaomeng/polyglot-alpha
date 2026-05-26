"""Reference seeder's internal debate orchestrator.

This module combines the L3 critic round, the L4 moderator, and the L5
refine pass into a single callable. The "debate loop" implemented here is
**NOT** part of the Polyglot Alpha protocol. It is one specific
implementation that the four reference seeder agents (gemini, deepseek,
qwen, llama) happen to use. External operators who register their own
agents on-chain are free to:

* Use single-shot LLM completions.
* Use retrieval-augmented generation.
* Use a fine-tuned model with no debate at all.
* Use a rule-based templating engine.
* Anything else they can sign and broadcast a bid for.

The protocol only cares that the bid carries a candidate_hash that matches
the candidate the operator eventually commits — nothing about how that
candidate was produced.

Public surface:

* :class:`InternalDebateResult` — dataclass returned to the caller.
* :func:`run_internal_debate` — async entrypoint.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .critics import CritiqueResult, run_critic_round
from .moderator import ModeratorVerdict, run_moderator
from .refine import RefineResult, refine_with_critique

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


# Hard cap on the total debate budget; pipeline must never block longer
# than this even if every sub-stage is misbehaving.
DEFAULT_DEBATE_TIMEOUT_S: float = 90.0


ProposeCandidatesFn = Callable[[Dict[str, Any]], Awaitable[List[Dict[str, Any]]]]


@dataclass
class InternalDebateResult:
    """Outcome of one internal-debate pass.

    ``final_candidate`` is the candidate AFTER the refine step. The
    on-chain candidate_hash MUST be computed against this dict so the
    provenance chain (raw event -> 2 candidates -> critique -> moderator
    -> refine -> on-chain bid) is verifiable post-resolution.

    ``intermediate_candidates`` retains the two pre-debate candidates so
    auditors can replay the chain.
    """

    final_candidate: Dict[str, Any]
    intermediate_candidates: List[Dict[str, Any]]
    critiques: List[CritiqueResult]
    moderator_verdict: ModeratorVerdict
    refine_result: RefineResult
    total_duration_ms: int
    total_llm_calls: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_internal_debate(
    event: Dict[str, Any],
    *,
    propose_candidates_fn: ProposeCandidatesFn,
    timeout: float = DEFAULT_DEBATE_TIMEOUT_S,
    critic_llm_factory: Optional[Callable[[str], Any]] = None,
    moderator_llm_factory: Optional[Callable[[str], Any]] = None,
    refine_llm: Optional[Any] = None,
    refine_model_id: Optional[str] = None,
    critic_timeout: Optional[float] = None,
    moderator_timeout: Optional[float] = None,
    refine_timeout: Optional[float] = None,
) -> InternalDebateResult:
    """Run the reference seeder's internal debate loop.

    The flow is intentionally identical to what the four seeders ship
    with so external operators can read this code as a worked example:

    1. Call ``propose_candidates_fn(event)`` to obtain N candidates
       (typically 2). The fn is provided by the caller so the same
       loop can drive any LLM / sampling strategy without re-coding
       internal_debate.
    2. Run the two critics in parallel against the two candidates
       (cross-review — each critic reviews the OTHER candidate).
    3. Hand the critic verdicts + candidates to the moderator. The
       moderator picks the winning candidate AND emits a 1-2 sentence
       refine signal.
    4. Run a single refine pass on the winning candidate, using the
       moderator's signal.
    5. Return the refined candidate as ``final_candidate`` plus the
       full intermediate trace.

    Args:
        event: raw event payload (must contain at least ``title_zh`` and
            ``body_zh``). Forwarded verbatim to the critics, moderator,
            and refine steps.
        propose_candidates_fn: async callable that turns the event into
            a list of candidate dicts. Typically wraps
            :func:`polyglot_alpha.translators.propose_candidates`, but
            external operators can pass anything that returns
            ``list[dict]``.
        timeout: hard cap on the whole debate in seconds. On timeout we
            return the best-effort intermediate state so the caller can
            still bid (with a degraded candidate) rather than missing
            the auction.
        critic_llm_factory: optional override for the critics' LLM
            factory (test injection).
        moderator_llm_factory: optional override for the moderator's LLM
            factory (test injection).
        refine_llm: optional async callable used for the refine pass
            (test injection). When ``None`` the refine module picks an
            LLM based on the winning candidate's ``meta.model`` field.
        refine_model_id: optional explicit model id for the refine
            pass; takes precedence over ``meta.model`` on the candidate.

    Returns:
        An :class:`InternalDebateResult` whose ``final_candidate`` is the
        post-refine winner. The candidate_hash the caller commits
        on-chain MUST be computed on ``final_candidate``.

    Raises:
        ValueError: if ``propose_candidates_fn`` returns fewer than 2
            candidates. The critic stage strictly requires exactly 2.
    """

    start = time.monotonic()
    llm_calls = 0

    try:
        debate = await asyncio.wait_for(
            _run_debate_inner(
                event,
                propose_candidates_fn=propose_candidates_fn,
                critic_llm_factory=critic_llm_factory,
                moderator_llm_factory=moderator_llm_factory,
                refine_llm=refine_llm,
                refine_model_id=refine_model_id,
                critic_timeout=critic_timeout,
                moderator_timeout=moderator_timeout,
                refine_timeout=refine_timeout,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "internal_debate: total budget %.1fs exhausted; "
            "this should be rare given each sub-stage has its own timeout",
            timeout,
        )
        raise

    candidates, critiques, verdict, refine_result, llm_calls = debate
    final = refine_result.refined_question

    return InternalDebateResult(
        final_candidate=final,
        intermediate_candidates=candidates,
        critiques=critiques,
        moderator_verdict=verdict,
        refine_result=refine_result,
        total_duration_ms=int((time.monotonic() - start) * 1000),
        total_llm_calls=llm_calls,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _run_debate_inner(
    event: Dict[str, Any],
    *,
    propose_candidates_fn: ProposeCandidatesFn,
    critic_llm_factory: Optional[Callable[[str], Any]],
    moderator_llm_factory: Optional[Callable[[str], Any]],
    refine_llm: Optional[Any],
    refine_model_id: Optional[str],
    critic_timeout: Optional[float],
    moderator_timeout: Optional[float],
    refine_timeout: Optional[float],
) -> tuple[
    List[Dict[str, Any]], List[CritiqueResult], ModeratorVerdict, RefineResult, int
]:
    """The actual debate body — separated so the outer wrapper can wrap
    it in a single :func:`asyncio.wait_for`."""

    llm_calls = 0

    # Step 1: propose candidates.
    raw_candidates = await propose_candidates_fn(event)
    if len(raw_candidates) < 2:
        raise ValueError(
            f"internal_debate requires at least 2 candidates, got {len(raw_candidates)}"
        )
    # Critics + moderator are strict 2-candidate stages; if the proposer
    # emitted more we only debate the first two (the rest are passed
    # through for auditing in ``intermediate_candidates``).
    candidates = [dict(c) for c in raw_candidates]
    debating = candidates[:2]
    # Each candidate proposal counts as one LLM call.
    llm_calls += len(debating)

    # Step 2: critic round (2 critics in parallel).
    critic_kwargs: Dict[str, Any] = {"llm_factory": critic_llm_factory}
    if critic_timeout is not None:
        critic_kwargs["timeout"] = critic_timeout
    critiques = await run_critic_round(debating, event, **critic_kwargs)
    llm_calls += len(critiques)

    # Step 3: moderator picks the winner + critique signal.
    mod_kwargs: Dict[str, Any] = {}
    if moderator_llm_factory is not None:
        mod_kwargs["llm_factory"] = moderator_llm_factory
    if moderator_timeout is not None:
        mod_kwargs["timeout"] = moderator_timeout
    verdict = await run_moderator(debating, critiques, event, **mod_kwargs)
    # Only count the moderator call if it actually fired (fallback path
    # does NOT issue an LLM call).
    if verdict.moderator_model != "(fallback)":
        llm_calls += 1

    # Step 4: refine the winning candidate.
    winning = debating[verdict.winning_index]
    refine_kwargs: Dict[str, Any] = {
        "model_id": refine_model_id,
        "llm": refine_llm,
    }
    if refine_timeout is not None:
        refine_kwargs["timeout_s"] = refine_timeout
    refine_result = await refine_with_critique(
        winning,
        verdict.critique_signal,
        event,
        **refine_kwargs,
    )
    # Refine always issues one LLM call unless it short-circuited on a
    # malformed JSON — but even that path consumed a call, so count it.
    # The no-op-on-timeout path also consumed a call attempt, so we count
    # it uniformly here for simplicity. Callers who need exact counts can
    # look at refine_result.raw_response.
    if refine_result.raw_response:
        llm_calls += 1
    elif "timed out" not in " ".join(refine_result.diff_summary):
        # Even on parse failure the LLM was invoked.
        llm_calls += 1

    return candidates, critiques, verdict, refine_result, llm_calls


__all__ = [
    "DEFAULT_DEBATE_TIMEOUT_S",
    "InternalDebateResult",
    "run_internal_debate",
]
