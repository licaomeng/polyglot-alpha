"""Synthesizer stage: merge two candidate translations into one Question.

This stage takes the candidates produced by the parallel translator debate
(Layer 2) and produces the single :class:`Question` that flows downstream
into quality_eval / refine / the 11-judge panel.

Honest note: this module calls an LLM (OpenRouter by default) to *merge*
insights from both candidates rather than to pick a winner. The LLM is
prompted to combine the best wording / resolution_criteria / end_date_iso
from across the candidates. When the LLM call fails for any reason
(missing API key, HTTP error, malformed JSON, timeout) we fall back to
the legacy heuristic — pick the candidate with the longest
``resolution_criteria`` — and emit a ``logger.warning`` so the fallback
path is never silently dressed up as an LLM result.

Public surface preserved (called sync from
:func:`polyglot_alpha.agents.dispatch._run_pipeline_schema` and
:meth:`polyglot_alpha.agents.base.BaseTranslatorAgent.run_pipeline`):

    synthesize(event: NewsEvent, candidates: List[TranslationCandidate]) -> Question
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from .schemas import NewsEvent, Question, TranslationCandidate

logger = logging.getLogger(__name__)

# OpenRouter model used for the merge call. Cheap + fast + good at structured
# JSON merge. Override via ``SYNTHESIZER_MODEL`` env var for experiments.
DEFAULT_SYNTHESIZER_MODEL = "anthropic/claude-haiku-4-5"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_TIMEOUT_SECONDS = 20.0

# Required keys on the merged dict — anything missing forces a fallback to
# the heuristic so we never produce a half-shaped Question downstream.
_REQUIRED_MERGE_KEYS = ("question_en", "resolution_criteria", "end_date_iso")


_SYSTEM_PROMPT = (
    "You are a prediction-market editor. Two analyst translators independently "
    "proposed a market question for the same news event. Your job is to "
    "synthesize a single best version that combines candidate A's strengths "
    "with candidate B's strengths (clearer wording from one, stronger "
    "resolution_source/criteria from the other, more defensible end_date_iso, "
    "etc.). Do NOT just pick one — actively merge. Output ONLY a JSON object "
    'with keys: "question_en", "resolution_criteria", "end_date_iso". No '
    "markdown, no commentary."
)


def synthesize(
    event: NewsEvent, candidates: List[TranslationCandidate]
) -> Question:
    """Merge candidates via an LLM into a single :class:`Question`.

    Falls back to the legacy heuristic (longest ``resolution_criteria``) on
    any LLM failure, with a ``logger.warning`` so the degradation is
    observable in logs and never silently mis-labelled as an LLM result.

    Signature preserved verbatim from the legacy heuristic implementation
    so callers in :mod:`polyglot_alpha.agents.dispatch` and
    :mod:`polyglot_alpha.agents.base` keep working unchanged.
    """

    if not candidates:
        raise ValueError("synthesize() requires at least one candidate")

    # Single-candidate short-circuit: nothing to merge.
    if len(candidates) == 1:
        only = candidates[0]
        return Question(
            event_id=event.event_id,
            question_en=only.question_en,
            resolution_criteria=only.resolution_criteria,
            end_date_iso=only.end_date_iso,
        )

    merged = _llm_merge(event, candidates)
    if merged is not None:
        return Question(
            event_id=event.event_id,
            question_en=str(merged["question_en"]).strip(),
            resolution_criteria=str(merged["resolution_criteria"]).strip(),
            end_date_iso=str(merged["end_date_iso"]).strip(),
        )

    # ---- Fallback: legacy heuristic. ALWAYS logged. ------------------- #
    logger.warning(
        "synthesizer: LLM merge unavailable; falling back to heuristic "
        "(longest resolution_criteria) for event_id=%s with %d candidates",
        event.event_id,
        len(candidates),
    )
    best = max(candidates, key=lambda c: len(c.resolution_criteria))
    return Question(
        event_id=event.event_id,
        question_en=best.question_en,
        resolution_criteria=best.resolution_criteria,
        end_date_iso=best.end_date_iso,
    )


# --------------------------------------------------------------------------- #
# Internal: LLM merge call.                                                   #
# --------------------------------------------------------------------------- #


def _llm_merge(
    event: NewsEvent, candidates: List[TranslationCandidate]
) -> Optional[Dict[str, Any]]:
    """Ask the LLM to merge the candidates. Return ``None`` on any failure.

    Sync HTTP call (synthesizer is invoked from inside an already-running
    event loop, so we cannot use ``asyncio.run`` and a sync ``httpx.Client``
    is the simplest correct option).
    """

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning(
            "synthesizer: OPENROUTER_API_KEY not set; cannot perform LLM merge"
        )
        return None

    model = os.getenv("SYNTHESIZER_MODEL", DEFAULT_SYNTHESIZER_MODEL)
    prompt = _build_prompt(event, candidates)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://polyglot-alpha.local",
        "X-Title": "polyglot-alpha-synthesizer",
    }

    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
            resp = client.post(_OPENROUTER_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        text = data["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.warning(
            "synthesizer: LLM HTTP call failed (model=%s): %s", model, exc
        )
        return None

    parsed = _parse_json_payload(text)
    if parsed is None:
        logger.warning(
            "synthesizer: LLM returned unparseable JSON (model=%s): %r",
            model,
            text[:200] if isinstance(text, str) else text,
        )
        return None

    if not all(parsed.get(k) for k in _REQUIRED_MERGE_KEYS):
        logger.warning(
            "synthesizer: LLM merge dict missing required keys; got %r",
            sorted(parsed.keys()) if isinstance(parsed, dict) else parsed,
        )
        return None

    return parsed


def _build_prompt(
    event: NewsEvent, candidates: List[TranslationCandidate]
) -> str:
    """Format the user prompt with the event context + the two candidates."""

    rendered: list[str] = []
    for label, cand in zip(("A", "B"), candidates[:2]):
        rendered.append(
            f"Candidate {label} (translator_id={cand.translator_id}):\n"
            f"  question_en: {cand.question_en}\n"
            f"  resolution_criteria: {cand.resolution_criteria}\n"
            f"  end_date_iso: {cand.end_date_iso}\n"
            f"  tags: {cand.tags}"
        )
    candidates_block = "\n\n".join(rendered)
    return (
        f"Event id: {event.event_id}\n"
        f"Event title (zh): {event.title_zh}\n"
        f"Event body (zh): {event.body_zh[:600]}\n\n"
        f"{candidates_block}\n\n"
        "Synthesize a single merged JSON object as instructed."
    )


def _parse_json_payload(text: Any) -> Optional[Dict[str, Any]]:
    """Best-effort JSON parse — tolerates ```` ```json ```` fences."""

    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj
