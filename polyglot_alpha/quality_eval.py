"""Quality evaluation: returns a 0-1 score for a synthesized Question.

The full pipeline uses an 11-judge panel; this stub provides a fast
deterministic heuristic so agents can self-estimate quality before
deciding how aggressively to bid.
"""

from __future__ import annotations

import logging

from .schemas import QualityScore, Question
from .stub_detector import is_stub, stub_reason

logger = logging.getLogger(__name__)

_PASS_THRESHOLD = 0.7
_MIN_RESOLUTION_LEN = 30
_MIN_QUESTION_LEN = 12


def score_question(question: Question) -> QualityScore:
    """Cheap heuristic score in [0, 1]. Counts presence of resolution
    criteria, question length, and a future end-date.

    Hard-fails (score=0.0, passed=False) when the question text or
    resolution criteria match a known LLM-glitch stub placeholder. The
    pre-W14-FIX-STUB heuristic was length-only, so a stub like
    ``"Resolves YES if the event occurs by the cutoff."`` (52 chars)
    sailed through. The explicit stub check makes the gate reject
    those events instead.
    """

    # ----- Stub short-circuit ------------------------------------------- #
    # If either field is a known placeholder, we know the upstream LLM
    # call glitched. Return ``score=0.0`` so the downstream pass gate
    # rejects the event. We do NOT call this "quality" — the rationale
    # is explicit so operators can tell stubs apart from genuinely poor
    # translations.
    if is_stub(question.question_en) or is_stub(question.resolution_criteria):
        leaked = stub_reason([question.question_en, question.resolution_criteria])
        logger.warning(
            "quality_eval: stub detected in question (event_id=%s); leaked_phrase=%r",
            question.event_id,
            leaked,
        )
        return QualityScore(
            score=0.0,
            rationale=f"stub_detected: {leaked}",
            passed=False,
        )

    # ----- Length / shape heuristic ------------------------------------- #
    score = 0.0
    rationale_parts: list[str] = []

    if len(question.question_en) >= _MIN_QUESTION_LEN:
        score += 0.35
    else:
        rationale_parts.append("question too short")

    if len(question.resolution_criteria) >= _MIN_RESOLUTION_LEN:
        score += 0.40
    else:
        rationale_parts.append("resolution criteria too short")

    if question.end_date_iso and "T" in question.end_date_iso:
        score += 0.25
    else:
        rationale_parts.append("missing/invalid end_date_iso")

    score = min(1.0, max(0.0, score))
    return QualityScore(
        score=score,
        rationale="; ".join(rationale_parts) or "all checks passed",
        passed=score >= _PASS_THRESHOLD,
    )
