"""Synthesizer stage: merge two candidate translations into one Question.

This stage takes the candidates produced by the parallel translator debate
(Layer 2) and produces the single :class:`Question` that flows downstream
into quality_eval / refine / the 11-judge panel.

Honest note: this module calls an LLM (Anthropic Claude Haiku 4.5 — the
single provider after the 2026-05 consolidation) to *merge* insights from
both candidates rather than to pick a winner. The LLM is prompted to
combine the best wording / resolution_criteria / end_date_iso from across
the candidates. When the LLM call fails for any reason (missing API key,
HTTP error, malformed JSON, timeout) we fall back to the legacy heuristic
— pick the candidate with the longest ``resolution_criteria`` — and emit
a ``logger.warning`` so the fallback path is never silently dressed up as
an LLM result.

Public surface preserved (called sync from
:func:`polyglot_alpha.agents.dispatch._run_pipeline_schema` and
:meth:`polyglot_alpha.agents.base.BaseTranslatorAgent.run_pipeline`):

    synthesize(event: NewsEvent, candidates: List[TranslationCandidate]) -> Question
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

# ``httpx`` is no longer used by this module after the single-provider
# consolidation (Anthropic SDK path only). The import is preserved as a
# module attribute so the legacy ``test_synthesizer.py`` fixtures that
# call ``patch.object(synthesizer.httpx, "Client")`` still find the
# attribute. The tests that actually expect the OpenRouter HTTP path
# now fail by design — see the consolidation report.
import httpx  # noqa: F401 — legacy test-fixture attribute, see comment above

from .llm import AnthropicLLM, CLAUDE_HAIKU, LLMError
from .schemas import NewsEvent, Question, TranslationCandidate

logger = logging.getLogger(__name__)

# Anthropic Haiku 4.5 is the cheap workhorse for the merge call.
DEFAULT_SYNTHESIZER_MODEL = CLAUDE_HAIKU
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
# Internal: LLM merge call (Anthropic-only after the 2026-05 consolidation). #
# --------------------------------------------------------------------------- #


def _llm_merge(
    event: NewsEvent, candidates: List[TranslationCandidate]
) -> Optional[Dict[str, Any]]:
    """Ask the LLM to merge the candidates. Return ``None`` on any failure.

    Resolution: when ``ANTHROPIC_API_KEY`` is set we route to the
    Anthropic SDK (Claude Haiku 4.5). Otherwise we warn and return
    ``None`` so the caller falls back to the longest-criteria heuristic.
    """

    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.warning(
            "synthesizer: ANTHROPIC_API_KEY is not set; cannot perform LLM merge"
        )
        return None

    return _anthropic_merge(event, candidates)


def _anthropic_merge(
    event: NewsEvent, candidates: List[TranslationCandidate]
) -> Optional[Dict[str, Any]]:
    """Anthropic SDK path. Synchronous wrapper around the async SDK."""

    model = os.getenv("SYNTHESIZER_MODEL", DEFAULT_SYNTHESIZER_MODEL)
    prompt = _build_prompt(event, candidates)

    async def _call() -> str:
        llm = AnthropicLLM(model=model)
        return await llm.complete(
            system=_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=1024,
            temperature=0.2,
        )

    try:
        # ``synthesize`` is invoked from inside an already-running event
        # loop, so we can't ``asyncio.run`` here; spin a fresh loop in a
        # thread instead.
        text = _run_async_in_thread(_call())
    except (LLMError, Exception) as exc:  # noqa: BLE001 — soft-fail any LLM error
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


def _run_async_in_thread(coro: Any) -> Any:
    """Run an awaitable to completion in a fresh thread-local event loop.

    ``synthesize`` is invoked from inside the already-running pipeline
    event loop, so ``asyncio.run`` would raise. We spin a new loop in a
    worker thread, run the coroutine there, and block the calling thread
    until it completes.
    """

    import threading

    result: dict[str, Any] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result["value"] = loop.run_until_complete(coro)
        except Exception as exc:  # noqa: BLE001 — surface to caller
            result["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=_DEFAULT_TIMEOUT_SECONDS + 5.0)
    if "error" in result:
        raise result["error"]
    return result.get("value")


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
