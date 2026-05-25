"""Quality evaluation: returns a 0-1 score for a synthesized Question.

The full pipeline uses an 11-judge panel; this stub provides a fast
deterministic heuristic so agents can self-estimate quality before
deciding how aggressively to bid.
"""

from __future__ import annotations

from .schemas import QualityScore, Question


_PASS_THRESHOLD = 0.7
_MIN_RESOLUTION_LEN = 30
_MIN_QUESTION_LEN = 12


def score_question(question: Question) -> QualityScore:
    """Cheap heuristic score in [0, 1]. Counts presence of resolution
    criteria, question length, and a future end-date."""

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
