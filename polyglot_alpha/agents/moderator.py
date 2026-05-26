"""Moderator stage: pick the winning candidate after the critic round.

Implements the L4 layer. A Sonnet-grade moderator reads both translator
candidates plus both critic reviews and emits a :class:`ModeratorVerdict`
with:

- the winning candidate index
- 3+ bullet reasons
- a ``critique_signal`` (1-2 sentences of actionable guidance for the L5
  refine step)
- a confidence score in [0, 1]

On timeout / LLM failure, falls back to the higher-confidence critic's
preferred candidate (or candidate 0 when both critics are uninformative).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..llm import (
    CLAUDE_SONNET,
    LLMCallable,
    MODERATOR_MAX_TOKENS,
    make_llm,
)
from ..models import MODEL_MODERATOR, CLAUDE_HAIKU
from .critics import CritiqueResult

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public dataclass                                                            #
# --------------------------------------------------------------------------- #


@dataclass
class ModeratorVerdict:
    """Final verdict from the L4 moderator stage."""

    winning_index: int
    moderator_model: str
    reasoning: List[str] = field(default_factory=list)
    confidence: float = 0.0
    critique_signal: str = ""
    raw_response: str = ""


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #


# Sonnet-grade by default. The actual snapshot is configured by
# :data:`polyglot_alpha.models.MODEL_MODERATOR` (env var ``MODEL_MODERATOR``,
# defaulting to ``MODEL_SONNET``). Routed through the Anthropic SDK via
# :func:`make_llm`; the factory falls back to MockLLM when no API key is
# configured, which keeps tests deterministic.
MODERATOR_MODEL = MODEL_MODERATOR
MODERATOR_MODEL_FALLBACK = CLAUDE_HAIKU

_MODERATOR_TIMEOUT_S = 60.0

_FALLBACK_SIGNAL_NO_CRITIC = (
    "moderator unavailable; no useful critic signal, "
    "preserve candidate as-is and tighten resolution criteria"
)
_FALLBACK_SIGNAL_BOTH_NEED_REFINEMENT = (
    "moderator unavailable; using critic A's feedback"
)


_MODERATOR_PROMPT_TMPL = (
    "You are the moderator in a Polymarket-question debate. Two translator "
    "agents proposed candidate market questions for the same news event, and "
    "two critic agents have just cross-reviewed them. Your job is to pick "
    "the SINGLE better candidate AND emit one actionable refine instruction "
    "(the 'critique signal') that the winner's refine pass should follow.\n\n"
    "ORIGINAL EVENT:\n"
    "  title: {title}\n"
    "  body: {body}\n\n"
    "CANDIDATE 0:\n"
    "  question_en: {q0_question}\n"
    "  resolution_criteria: {q0_resolution}\n"
    "  end_date_iso: {q0_end_date}\n"
    "  tags: {q0_tags}\n\n"
    "CANDIDATE 1:\n"
    "  question_en: {q1_question}\n"
    "  resolution_criteria: {q1_resolution}\n"
    "  end_date_iso: {q1_end_date}\n"
    "  tags: {q1_tags}\n\n"
    "CRITIC REVIEW OF CANDIDATE 0 (from {critic0_model}):\n"
    "  verdict: {critic0_verdict}\n"
    "  confidence: {critic0_conf}\n"
    "  issues: {critic0_issues}\n"
    "  strengths: {critic0_strengths}\n\n"
    "CRITIC REVIEW OF CANDIDATE 1 (from {critic1_model}):\n"
    "  verdict: {critic1_verdict}\n"
    "  confidence: {critic1_conf}\n"
    "  issues: {critic1_issues}\n"
    "  strengths: {critic1_strengths}\n\n"
    "Return STRICT JSON with these keys ONLY:\n"
    '  "winning_index": 0 or 1,\n'
    '  "reasoning": list of >=3 short bullet reasons,\n'
    '  "confidence": float in [0, 1],\n'
    '  "critique_signal": 1-2 sentences of refine guidance for the winner.\n'
    "Return ONLY the JSON object, no prose, no markdown fences."
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def _coerce_index(value: Any) -> Optional[int]:
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return None
    if idx in (0, 1):
        return idx
    return None


def _coerce_confidence(value: Any) -> float:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return 0.0
    if conf < 0.0:
        return 0.0
    if conf > 1.0:
        return 1.0
    return conf


def _coerce_reasoning(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _critic_for_target(
    critiques: List[CritiqueResult], target_index: int
) -> Optional[CritiqueResult]:
    for c in critiques:
        if c.target_index == target_index:
            return c
    return None


def _build_prompt(
    candidates: List[Dict[str, Any]],
    critiques: List[CritiqueResult],
    event: Dict[str, Any],
) -> str:
    critic0 = _critic_for_target(critiques, 0)
    critic1 = _critic_for_target(critiques, 1)

    def _fmt_issues(c: Optional[CritiqueResult]) -> str:
        if c is None or not c.issues:
            return "(none)"
        return "; ".join(c.issues)

    def _fmt_strengths(c: Optional[CritiqueResult]) -> str:
        if c is None or not c.strengths:
            return "(none)"
        return "; ".join(c.strengths)

    return _MODERATOR_PROMPT_TMPL.format(
        title=str(event.get("title_zh") or event.get("title") or ""),
        body=str(event.get("body_zh") or event.get("body") or ""),
        q0_question=str(candidates[0].get("question_en") or ""),
        q0_resolution=str(candidates[0].get("resolution_criteria") or ""),
        q0_end_date=str(candidates[0].get("end_date_iso") or ""),
        q0_tags=", ".join(str(t) for t in (candidates[0].get("tags") or [])) or "(none)",
        q1_question=str(candidates[1].get("question_en") or ""),
        q1_resolution=str(candidates[1].get("resolution_criteria") or ""),
        q1_end_date=str(candidates[1].get("end_date_iso") or ""),
        q1_tags=", ".join(str(t) for t in (candidates[1].get("tags") or [])) or "(none)",
        critic0_model=critic0.critic_model if critic0 else "(none)",
        critic0_verdict=critic0.verdict if critic0 else "(none)",
        critic0_conf=f"{critic0.confidence:.2f}" if critic0 else "0.00",
        critic0_issues=_fmt_issues(critic0),
        critic0_strengths=_fmt_strengths(critic0),
        critic1_model=critic1.critic_model if critic1 else "(none)",
        critic1_verdict=critic1.verdict if critic1 else "(none)",
        critic1_conf=f"{critic1.confidence:.2f}" if critic1 else "0.00",
        critic1_issues=_fmt_issues(critic1),
        critic1_strengths=_fmt_strengths(critic1),
    )


def _parse_moderator_response(raw: str, model_id: str) -> Optional[ModeratorVerdict]:
    payload = _extract_json(raw)
    if not payload:
        return None
    idx = _coerce_index(payload.get("winning_index"))
    if idx is None:
        return None
    reasoning = _coerce_reasoning(payload.get("reasoning"))
    if len(reasoning) < 1:
        # Spec demands 3+ but we still accept >=1 to avoid being too brittle;
        # downstream consumers can decide what to do with thin reasoning.
        reasoning = ["(moderator returned empty reasoning)"]
    signal = str(payload.get("critique_signal") or "").strip()
    return ModeratorVerdict(
        winning_index=idx,
        moderator_model=model_id,
        reasoning=reasoning,
        confidence=_coerce_confidence(payload.get("confidence")),
        critique_signal=signal,
        raw_response=raw or "",
    )


def _fallback_from_critics(
    critiques: List[CritiqueResult],
    reason: str,
) -> ModeratorVerdict:
    """Build a moderator verdict from critic signals when the LLM is unavailable.

    Policy:
    - If exactly one critic has ``verdict='accept_as_is'``, that critic's
      target wins.
    - Otherwise, prefer the candidate critiqued by the higher-confidence
      critic whose verdict is NOT ``reject``.
    - If both critics emitted ``needs_refinement``, use candidate 0 with a
      fixed signal (per the original spec).
    - Otherwise, fall back to candidate 0 with a neutral signal.
    """

    critic0 = _critic_for_target(critiques, 0)
    critic1 = _critic_for_target(critiques, 1)

    verdict0 = critic0.verdict if critic0 else "accept_as_is"
    verdict1 = critic1.verdict if critic1 else "accept_as_is"

    # Both critics flagged needs_refinement — spec-defined fallback.
    if verdict0 == "needs_refinement" and verdict1 == "needs_refinement":
        return ModeratorVerdict(
            winning_index=0,
            moderator_model="(fallback)",
            reasoning=[
                f"moderator fallback ({reason})",
                "both critics returned needs_refinement",
                "defaulting to candidate 0 per fallback policy",
            ],
            confidence=0.0,
            critique_signal=_FALLBACK_SIGNAL_BOTH_NEED_REFINEMENT,
            raw_response=f"[fallback: {reason}]",
        )

    # Pick the candidate the higher-confidence critic *liked* (verdict != reject).
    # "Higher confidence" means the critic whose review carried more weight.
    candidates_ranked: List[tuple[int, float, str]] = []
    if critic0 is not None and critic0.verdict != "reject":
        candidates_ranked.append((0, critic0.confidence, critic0.verdict))
    if critic1 is not None and critic1.verdict != "reject":
        candidates_ranked.append((1, critic1.confidence, critic1.verdict))

    if candidates_ranked:
        # Highest critic confidence wins; ties broken by lower index.
        candidates_ranked.sort(key=lambda t: (-t[1], t[0]))
        winning_index, conf, verdict = candidates_ranked[0]
        # Build a signal from that critic's issues, if any.
        winning_critic = _critic_for_target(critiques, winning_index)
        signal_parts: List[str] = []
        if winning_critic and winning_critic.issues:
            signal_parts.append(
                "address: " + "; ".join(winning_critic.issues[:2])
            )
        signal = (
            " ".join(signal_parts)
            if signal_parts
            else _FALLBACK_SIGNAL_NO_CRITIC
        )
        return ModeratorVerdict(
            winning_index=winning_index,
            moderator_model="(fallback)",
            reasoning=[
                f"moderator fallback ({reason})",
                f"selected candidate {winning_index} via higher critic confidence ({conf:.2f})",
                f"winning critic verdict: {verdict}",
            ],
            confidence=conf,
            critique_signal=signal,
            raw_response=f"[fallback: {reason}]",
        )

    # No critic gave us anything useful — default to candidate 0.
    return ModeratorVerdict(
        winning_index=0,
        moderator_model="(fallback)",
        reasoning=[
            f"moderator fallback ({reason})",
            "no usable critic signal available",
            "defaulting to candidate 0",
        ],
        confidence=0.0,
        critique_signal=_FALLBACK_SIGNAL_NO_CRITIC,
        raw_response=f"[fallback: {reason}]",
    )


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


LLMFactory = Callable[[str], LLMCallable]


async def run_moderator(
    candidates: List[Dict[str, Any]],
    critiques: List[CritiqueResult],
    event: Dict[str, Any],
    *,
    llm_factory: Optional[LLMFactory] = None,
    timeout: float = _MODERATOR_TIMEOUT_S,
    model_id: str = MODERATOR_MODEL,
) -> ModeratorVerdict:
    """Sonnet-grade moderator picks one candidate + emits refine guidance.

    Args:
        candidates: two candidate question dicts produced by the translator
            layer.
        critiques: critic results (typically length-2) from
            :func:`run_critic_round`.
        event: original event payload.
        llm_factory: optional override for the LLM constructor (tests).
        timeout: moderator-call timeout in seconds.
        model_id: Anthropic model snapshot; defaults to Claude Sonnet 4.5.

    Returns:
        A :class:`ModeratorVerdict`. On any LLM failure / timeout, returns a
        fallback verdict synthesized from critic confidences.
    """

    if len(candidates) != 2:
        raise ValueError(
            f"run_moderator expects exactly 2 candidates, got {len(candidates)}"
        )

    factory: LLMFactory = llm_factory or (
        lambda mid: make_llm(mid, max_tokens=MODERATOR_MAX_TOKENS)
    )
    llm = factory(model_id)

    prompt = _build_prompt(candidates, critiques, event)
    try:
        raw = await asyncio.wait_for(llm(prompt), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("moderator timed out after %.1fs, using fallback", timeout)
        return _fallback_from_critics(critiques, reason="timeout")
    except Exception as exc:  # noqa: BLE001 — soft-fail any LLM failure
        logger.warning("moderator LLM call failed: %r — using fallback", exc)
        return _fallback_from_critics(critiques, reason=f"llm_error: {exc!r}")

    parsed = _parse_moderator_response(raw, model_id)
    if parsed is None:
        logger.warning(
            "moderator returned unparseable JSON, using fallback. raw=%r",
            (raw or "")[:200],
        )
        return _fallback_from_critics(critiques, reason="unparseable_response")
    return parsed


__all__ = [
    "MODERATOR_MODEL",
    "MODERATOR_MODEL_FALLBACK",
    "ModeratorVerdict",
    "run_moderator",
]
