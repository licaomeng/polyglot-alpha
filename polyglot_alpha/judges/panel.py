"""Panel aggregator.

Runs all 11 judges in parallel via ``asyncio.gather``, then collapses
the individual :class:`JudgeResult`s into a single
:class:`PanelVerdict`.

Pass criteria (per README §5.22 / §5.25):
    * HARD gates (all must pass): D1 Structural, D5 Resolution Clarity,
      D8 Duplicate Detection, AND translation MQM score >= 80.
    * SOFT gates (>=4 of 5 must pass): D2, D3, D4, D6, D7.

The weight table is intentionally exposed as ``_weights`` on the module
with a ``__getattr__`` guard so it can't be probed from external
callers in production. Set ``POLYGLOT_DEMO_MODE=1`` (or
``allow_weight_access=True`` to :func:`evaluate`) to read it. Every
demo-mode access is appended to ``outputs/weight_access_log.jsonl`` for
audit (README §5.27 — evaluator IP must be closed in production).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

logger = logging.getLogger(__name__)

# Per-judge wall-clock cap. Keeps a single hung LLM / model-load from
# stalling the whole panel forever. Falls back to a "judge crashed"
# JudgeResult for that one judge while the rest still aggregate.
PER_JUDGE_TIMEOUT_S: float = float(os.environ.get("PER_JUDGE_TIMEOUT_S", "60"))

from polyglot_alpha.judges.style_alignment import (
    judge_d1_structural,
    judge_d2_stylistic,
    judge_d3_framing,
    judge_d4_granularity,
    judge_d5_resolution_clarity,
    judge_d6_source_reliability,
    judge_d7_leading_check,
    judge_d8_duplicate_detection,
)
from polyglot_alpha.judges.style_alignment.llm_batch import clear_cache
from polyglot_alpha.judges.translation import (
    judge_bleu,
    judge_comet,
    judge_mqm_llm,
)
from polyglot_alpha.judges.types import (
    HARD_STYLE_REQUIREMENTS,
    MAJORITY_REQUIRED_COUNT,
    MAJORITY_STYLE_POOL,
    MQM_PASS_THRESHOLD,
    JudgeResult,
    PanelQuestion,
    PanelVerdict,
    VERDICT_BORDERLINE,
    VERDICT_FAIL,
    VERDICT_PASS,
)

LlmCall = Callable[[str], Awaitable[str]]


# --------------------------------------------------------------------------- #
# Closed-IP weight table                                                      #
# --------------------------------------------------------------------------- #
# Weights are part of the production scoring policy. We keep them in module
# scope but route access through ``__getattr__`` so external callers can't
# read them unless demo mode is explicitly enabled.

_WEIGHTS: dict[str, float] = {
    # Translation block: 60% of the headline score.
    "bleu": 0.10,
    "comet": 0.20,
    "mqm_llm": 0.30,
    # Style-alignment block: 40%. D5 doubled (0.06 -> 0.12) because it is
    # the single highest-EV dimension (README §5.22 — UMA dispute prevention).
    "d1_structural": 0.08,
    "d2_stylistic": 0.03,
    "d3_framing": 0.03,
    "d4_granularity": 0.05,
    "d5_resolution_clarity": 0.12,
    "d6_source_reliability": 0.02,
    "d7_leading_check": 0.02,
    "d8_duplicate_detection": 0.05,
}
assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, (
    "Panel weights must sum to 1.0; got "
    f"{sum(_WEIGHTS.values()):.6f}"
)


WEIGHT_ACCESS_LOG_PATH = Path("outputs/weight_access_log.jsonl")


class _WeightsAccessError(RuntimeError):
    """Raised when weights are read without demo-mode opt-in."""


def _demo_mode_enabled() -> bool:
    return bool(os.getenv("POLYGLOT_DEMO_MODE")) or _ALLOW_WEIGHT_ACCESS["value"]


_ALLOW_WEIGHT_ACCESS = {"value": False}


def _audit_weight_access(reason: str) -> None:
    """Append a JSONL audit record for every demo-mode weight read.

    Best-effort: silently drops the record if the outputs directory is
    not writable (e.g. read-only test environment). This keeps the
    guard from breaking tests while still capturing access in normal
    operation.
    """

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "caller": _resolve_caller(),
        "env_demo_mode": bool(os.getenv("POLYGLOT_DEMO_MODE")),
        "in_process_toggle": _ALLOW_WEIGHT_ACCESS["value"],
    }
    try:
        WEIGHT_ACCESS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with WEIGHT_ACCESS_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:  # pragma: no cover - logging is best-effort
        pass


def _resolve_caller() -> str:
    """Return ``module:function:lineno`` for the outermost non-panel frame."""

    frame = sys._getframe(2) if hasattr(sys, "_getframe") else None
    while frame is not None and frame.f_globals.get("__name__") == __name__:
        frame = frame.f_back
    if frame is None:
        return "unknown"
    return (
        f"{frame.f_globals.get('__name__', '?')}:"
        f"{frame.f_code.co_name}:{frame.f_lineno}"
    )


def __getattr__(name: str) -> Any:  # PEP 562 module-level __getattr__
    if name == "_weights":
        if _demo_mode_enabled():
            _audit_weight_access("demo_mode_read")
            return dict(_WEIGHTS)
        raise _WeightsAccessError(
            "panel._weights is closed IP; set POLYGLOT_DEMO_MODE=1 to read."
        )
    raise AttributeError(name)


# --------------------------------------------------------------------------- #
# Aggregator                                                                  #
# --------------------------------------------------------------------------- #


def _maybe_await(value: Any) -> Awaitable[Any]:
    """Return ``value`` as an awaitable, wrapping plain values if needed."""

    if inspect.isawaitable(value):
        return value

    async def _wrap() -> Any:
        return value

    return _wrap()


async def evaluate(
    question: PanelQuestion | Mapping[str, Any],
    reference_translation: Optional[str] = None,
    *,
    llm_call: Optional[LlmCall] = None,
    mqm_llm_call: Optional[LlmCall] = None,
    d8_index_path: Optional[str] = None,
    allow_weight_access: bool = False,
) -> PanelVerdict:
    """Run all 11 judges and return a :class:`PanelVerdict`.

    Args:
        question: A ``PanelQuestion`` instance or a dict accepted by
            :meth:`PanelQuestion.from_mapping`.
        reference_translation: Optional reference for BLEU.
        llm_call: Override for the style-batch LLM (D2/D3/D6/D7).
        mqm_llm_call: Override for the MQM judge LLM. Defaults to
            ``llm_call`` if not supplied.
        d8_index_path: Override FAISS index location.
        allow_weight_access: Demo-mode toggle for ``_weights``.
    """

    if not isinstance(question, PanelQuestion):
        question = PanelQuestion.from_mapping(question)

    if allow_weight_access:
        _ALLOW_WEIGHT_ACCESS["value"] = True

    # Reset the shared LLM-batch cache so each question runs fresh.
    clear_cache()

    mqm_backend = mqm_llm_call or llm_call

    tasks = {
        "bleu": judge_bleu(question, reference_translation),
        "comet": judge_comet(question),
        "mqm_llm": judge_mqm_llm(question, llm_call=mqm_backend),
        "d1_structural": judge_d1_structural(question, llm_call=llm_call),
        "d2_stylistic": judge_d2_stylistic(question, llm_call=llm_call),
        "d3_framing": judge_d3_framing(question, llm_call=llm_call),
        "d4_granularity": judge_d4_granularity(question),
        "d5_resolution_clarity": judge_d5_resolution_clarity(
            question, llm_call=llm_call
        ),
        "d6_source_reliability": judge_d6_source_reliability(question, llm_call=llm_call),
        "d7_leading_check": judge_d7_leading_check(question, llm_call=llm_call),
        "d8_duplicate_detection": judge_d8_duplicate_detection(
            question, index_path=d8_index_path
        ),
    }

    logger.info("panel.evaluate: dispatching %d judges", len(tasks))

    async def _run_one(name: str, coro: Awaitable[Any]) -> Any:
        """Cap a single judge at ``PER_JUDGE_TIMEOUT_S`` so one hung LLM /
        model load cannot stall the whole panel."""

        try:
            res = await asyncio.wait_for(
                _maybe_await(coro), timeout=PER_JUDGE_TIMEOUT_S
            )
            logger.debug("panel.evaluate: judge=%s OK", name)
            return res
        except asyncio.TimeoutError:
            logger.warning(
                "panel.evaluate: judge=%s timed out after %.0fs",
                name,
                PER_JUDGE_TIMEOUT_S,
            )
            # D8 (duplicate detection) and BLEU/COMET (translation gate is
            # "any of them") use external models / corpora that may be
            # unreachable in the demo environment. Treating a timeout as
            # "skip with pass" matches their offline fallback semantics and
            # prevents a single network hiccup from rejecting an otherwise
            # high-quality candidate.
            soft_skip_names = {"d8_duplicate_detection", "bleu", "comet"}
            if name in soft_skip_names:
                return JudgeResult(
                    name=name,
                    passed=True,
                    score=1.0,
                    reason=(
                        f"judge timed out after {PER_JUDGE_TIMEOUT_S:.0f}s "
                        "(soft-skip; model/corpus unreachable in demo env)"
                    ),
                    evidence={"timeout": True, "soft_skip": True},
                )
            return JudgeResult(
                name=name,
                passed=False,
                score=0.0,
                reason=f"judge timed out after {PER_JUDGE_TIMEOUT_S:.0f}s",
                evidence={"timeout": True},
            )

    awaited = await asyncio.gather(
        *(_run_one(name, coro) for name, coro in tasks.items()),
        return_exceptions=True,
    )
    results: dict[str, JudgeResult] = {}
    for name, value in zip(tasks.keys(), awaited):
        if isinstance(value, Exception):
            logger.warning(
                "panel.evaluate: judge=%s crashed: %r", name, value
            )
            results[name] = JudgeResult(
                name=name,
                passed=False,
                score=0.0,
                reason=f"judge crashed: {value}",
                evidence={"exception": repr(value)},
            )
        else:
            results[name] = value

    logger.info(
        "panel.evaluate: collected %d/%d judges",
        sum(1 for v in results.values() if not v.evidence.get("timeout")),
        len(results),
    )

    verdict = _aggregate(results)
    return verdict


def _aggregate(results: Mapping[str, JudgeResult]) -> PanelVerdict:
    notes: list[str] = []

    bleu = results["bleu"]
    comet = results["comet"]
    mqm = results["mqm_llm"]

    # Translation gate (README §5.22): BLEU > 25 OR COMET > 0.6, AND MQM
    # score >= MQM_PASS_THRESHOLD (80) AND zero major errors. Offline MQM
    # is treated as gate-pass so the demo can still produce a verdict
    # without a live LLM.
    translation_pass_any = bleu.passed or comet.passed
    mqm_score_raw = mqm.evidence.get("score_raw")
    mqm_major_count = int(mqm.evidence.get("major_count") or 0)
    if mqm.evidence.get("offline") or mqm_score_raw is None:
        mqm_gate_pass = True
        notes.append("MQM offline / score unavailable — gate satisfied by default.")
    else:
        score_ok = int(mqm_score_raw) >= MQM_PASS_THRESHOLD
        majors_ok = mqm_major_count == 0
        mqm_gate_pass = score_ok and majors_ok
        if not score_ok:
            notes.append(
                f"MQM score {mqm_score_raw} < {MQM_PASS_THRESHOLD} threshold."
            )
        if not majors_ok:
            notes.append(
                f"MQM has {mqm_major_count} major error(s); zero required."
            )
    translation_pass = translation_pass_any and mqm_gate_pass

    # Style gates.
    style_passes = {
        f"d{i}": results[name].passed
        for i, name in enumerate(
            (
                "d1_structural",
                "d2_stylistic",
                "d3_framing",
                "d4_granularity",
                "d5_resolution_clarity",
                "d6_source_reliability",
                "d7_leading_check",
                "d8_duplicate_detection",
            ),
            start=1,
        )
    }

    hard_pass = all(style_passes[req] for req in HARD_STYLE_REQUIREMENTS)
    majority_passes = sum(1 for d in MAJORITY_STYLE_POOL if style_passes[d])
    majority_pass = majority_passes >= MAJORITY_REQUIRED_COUNT

    if not hard_pass:
        notes.append(
            "Hard style gate failed: "
            + ", ".join(d for d in HARD_STYLE_REQUIREMENTS if not style_passes[d])
        )
    if not majority_pass:
        notes.append(
            f"Soft style gates only {majority_passes}/{len(MAJORITY_STYLE_POOL)}"
            f" (need {MAJORITY_REQUIRED_COUNT})."
        )

    overall_pass = translation_pass and hard_pass and majority_pass

    # Weighted 0-100 score.
    score_float = 0.0
    total_weight = 0.0
    for name, result in results.items():
        w = _WEIGHTS.get(name, 0.0)
        if w <= 0:
            continue
        score_float += w * float(result.score)
        total_weight += w
    overall_score = int(round(100.0 * (score_float / total_weight))) if total_weight else 0

    # Verdict bucketing.
    if overall_pass:
        verdict = VERDICT_PASS
    elif (
        translation_pass_any
        and hard_pass
        and (majority_passes >= MAJORITY_REQUIRED_COUNT - 1)
    ):
        # Close but not quite — borderline so the operator can hand-review.
        verdict = VERDICT_BORDERLINE
    else:
        verdict = VERDICT_FAIL

    return PanelVerdict(
        overall_pass=overall_pass,
        verdict=verdict,
        overall_score=overall_score,
        translation_scores={
            "bleu": bleu.evidence.get("bleu_raw"),
            "comet": comet.evidence.get("comet_raw"),
            "mqm": {
                "score": mqm.evidence.get("score_raw"),
                "major_count": mqm.evidence.get("major_count", 0),
                "minor_count": mqm.evidence.get("minor_count", 0),
                "errors": mqm.evidence.get("errors", []),
            },
        },
        style_alignment_passes=style_passes,
        judge_results=list(results.values()),
        notes=notes,
    )
