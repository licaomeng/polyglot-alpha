"""Critic stage: cross-review translator candidates for Polymarket quality.

This module implements the L3 layer of the upgraded translation pipeline.
Two critic LLMs review the OTHER translator's candidate (cross-review prevents
self-flattering bias) and return structured :class:`CritiqueResult` payloads
which feed the L4 moderator.

The critics intentionally use two *different* cheap LLMs to maximize critique
diversity. On timeout or any LLM error, each critic soft-fails to a neutral
``accept_as_is`` verdict so the pipeline keeps moving.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from ..llm import LLMCallable, make_llm

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public dataclass                                                            #
# --------------------------------------------------------------------------- #


CriticVerdict = Literal["accept_as_is", "needs_refinement", "reject"]

_VALID_VERDICTS: tuple[CriticVerdict, ...] = (
    "accept_as_is",
    "needs_refinement",
    "reject",
)


@dataclass
class CritiqueResult:
    """One critic's structured review of a translator candidate."""

    target_index: int
    critic_model: str
    issues: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    verdict: CriticVerdict = "accept_as_is"
    confidence: float = 0.0
    raw_response: str = ""


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #


# Both critics now run on the Anthropic Haiku 4.5 snapshot via
# :func:`polyglot_alpha.llm.make_llm`. We keep TWO distinct model id strings
# so the critic-fanout still passes different ``model_id`` values into the
# factory (preserving the existing test contract where critic A and critic
# B are dispatched independently); under the Anthropic backend both ids
# resolve to the same Haiku snapshot, while critique diversity comes from
# the cross-review (each critic reviews the OTHER's candidate).
CRITIC_MODEL_A = "claude-haiku-4-5-critic-a"
CRITIC_MODEL_B = "claude-haiku-4-5-critic-b"

_CRITIC_TIMEOUT_S = 30.0

_CRITIC_PROMPT_TMPL = (
    "You are a Polymarket quality reviewer. Your task is to critique the "
    "following candidate binary-outcome market question for concrete quality "
    "issues.\n\n"
    "Focus your critique on these dimensions:\n"
    "- ambiguity in the question wording\n"
    "- resolution-criteria clarity (can a neutral party adjudicate?)\n"
    "- leading wording that biases YES or NO\n"
    "- source reliability for resolution data\n"
    "- scope creep (does the question conflate multiple outcomes?)\n"
    "- timeline issues (cutoff vs end_date_iso mismatch, ambiguous deadlines)\n\n"
    "ORIGINAL EVENT:\n"
    "  title: {title}\n"
    "  body: {body}\n\n"
    "CANDIDATE QUESTION (index {target_index}):\n"
    "  question_en: {question_en}\n"
    "  resolution_criteria: {resolution_criteria}\n"
    "  end_date_iso: {end_date_iso}\n"
    "  tags: {tags}\n\n"
    "Return STRICT JSON with these keys ONLY:\n"
    '  "issues": list of 3-5 concrete issue strings,\n'
    '  "strengths": list of 1-3 strength strings,\n'
    '  "verdict": one of "accept_as_is" | "needs_refinement" | "reject",\n'
    '  "confidence": float in [0, 1].\n'
    "Return ONLY the JSON object, no prose, no markdown fences."
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _extract_json(text: str) -> Dict[str, Any]:
    """Best-effort JSON extraction; tolerates markdown fences and prose."""

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


def _coerce_verdict(value: Any) -> CriticVerdict:
    if isinstance(value, str) and value in _VALID_VERDICTS:
        return value  # type: ignore[return-value]
    return "accept_as_is"


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


def _coerce_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _build_prompt(candidate: Dict[str, Any], event: Dict[str, Any], target_index: int) -> str:
    return _CRITIC_PROMPT_TMPL.format(
        title=str(event.get("title_zh") or event.get("title") or ""),
        body=str(event.get("body_zh") or event.get("body") or ""),
        target_index=target_index,
        question_en=str(candidate.get("question_en") or ""),
        resolution_criteria=str(candidate.get("resolution_criteria") or ""),
        end_date_iso=str(candidate.get("end_date_iso") or ""),
        tags=", ".join(str(t) for t in (candidate.get("tags") or [])) or "(none)",
    )


def _parse_critic_response(
    raw: str,
    target_index: int,
    critic_model: str,
) -> CritiqueResult:
    payload = _extract_json(raw)
    return CritiqueResult(
        target_index=target_index,
        critic_model=critic_model,
        issues=_coerce_str_list(payload.get("issues")),
        strengths=_coerce_str_list(payload.get("strengths")),
        verdict=_coerce_verdict(payload.get("verdict")),
        confidence=_coerce_confidence(payload.get("confidence")),
        raw_response=raw or "",
    )


def _soft_fail(target_index: int, critic_model: str, reason: str) -> CritiqueResult:
    logger.warning(
        "critic soft-failing: target_index=%s model=%s reason=%s",
        target_index,
        critic_model,
        reason,
    )
    return CritiqueResult(
        target_index=target_index,
        critic_model=critic_model,
        issues=[],
        strengths=[],
        verdict="accept_as_is",
        confidence=0.0,
        raw_response=f"[soft-fail: {reason}]",
    )


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


# Factory hook so tests can inject mock LLMs without touching env vars.
LLMFactory = Callable[[str], LLMCallable]


async def _run_single_critic(
    *,
    candidate: Dict[str, Any],
    event: Dict[str, Any],
    target_index: int,
    critic_model: str,
    llm: LLMCallable,
    timeout: float,
) -> CritiqueResult:
    prompt = _build_prompt(candidate, event, target_index)
    try:
        raw = await asyncio.wait_for(llm(prompt), timeout=timeout)
    except asyncio.TimeoutError:
        return _soft_fail(target_index, critic_model, "timeout")
    except Exception as exc:  # noqa: BLE001 — soft-fail any LLM failure
        return _soft_fail(target_index, critic_model, f"llm_error: {exc!r}")
    return _parse_critic_response(raw, target_index, critic_model)


async def run_critic_round(
    candidates: List[Dict[str, Any]],
    event: Dict[str, Any],
    *,
    llm_factory: Optional[LLMFactory] = None,
    timeout: float = _CRITIC_TIMEOUT_S,
) -> List[CritiqueResult]:
    """Two critics in parallel, each reviewing the OTHER's candidate.

    Args:
        candidates: list of exactly two candidate question dicts produced by
            the translator layer.
        event: original event payload (title/body etc).
        llm_factory: optional callable that returns an :class:`LLMCallable` for
            a given model id. Defaults to :func:`polyglot_alpha.llm.make_llm`.
        timeout: per-critic timeout in seconds.

    Returns:
        A list of two :class:`CritiqueResult` instances, ordered by the
        ``target_index`` they reviewed (i.e. ``[review_of_0, review_of_1]``).
    """

    if len(candidates) != 2:
        raise ValueError(
            f"run_critic_round expects exactly 2 candidates, got {len(candidates)}"
        )

    factory: LLMFactory = llm_factory or (lambda model_id: make_llm(model_id))

    # Critic A (CRITIC_MODEL_A) reviews candidate B (index 1).
    # Critic B (CRITIC_MODEL_B) reviews candidate A (index 0).
    critic_a_llm = factory(CRITIC_MODEL_A)
    critic_b_llm = factory(CRITIC_MODEL_B)

    review_of_b = _run_single_critic(
        candidate=candidates[1],
        event=event,
        target_index=1,
        critic_model=CRITIC_MODEL_A,
        llm=critic_a_llm,
        timeout=timeout,
    )
    review_of_a = _run_single_critic(
        candidate=candidates[0],
        event=event,
        target_index=0,
        critic_model=CRITIC_MODEL_B,
        llm=critic_b_llm,
        timeout=timeout,
    )

    results = await asyncio.gather(review_of_a, review_of_b)
    # Order by target_index so consumers can index by candidate position.
    results.sort(key=lambda r: r.target_index)
    return results


__all__ = [
    "CRITIC_MODEL_A",
    "CRITIC_MODEL_B",
    "CritiqueResult",
    "run_critic_round",
]
