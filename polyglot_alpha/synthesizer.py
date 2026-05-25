"""Synthesizer stage: pick / merge candidate translations into one Question."""

from __future__ import annotations

from typing import List

from .schemas import NewsEvent, Question, TranslationCandidate


def synthesize(
    event: NewsEvent, candidates: List[TranslationCandidate]
) -> Question:
    """Choose the longest-resolution-criteria candidate (heuristic) and emit
    a :class:`Question`. The full pipeline will replace this with an LLM
    reranker; the agent code only depends on the returned shape."""

    if not candidates:
        raise ValueError("synthesize() requires at least one candidate")
    best = max(candidates, key=lambda c: len(c.resolution_criteria))
    return Question(
        event_id=event.event_id,
        question_en=best.question_en,
        resolution_criteria=best.resolution_criteria,
        end_date_iso=best.end_date_iso,
    )
