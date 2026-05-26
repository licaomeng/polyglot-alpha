"""Translator stage of the pipeline.

Takes the analyst reports + raw event and emits one or more candidate
Polymarket-shaped market questions. The agent code calls
``propose_candidates`` and forwards the candidates to ``synthesizer``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List

from .llm import LLMCallable
from .schemas import AnalystReport, NewsEvent, TranslationCandidate
from .stub_detector import KNOWN_STUB_PHRASES

logger = logging.getLogger(__name__)


_CANDIDATE_COUNT = 2

# Generic placeholders emitted when the LLM returns empty/unparseable
# JSON. Kept as module-level constants so the fallback path is explicit
# and the strings stay in sync with :mod:`polyglot_alpha.stub_detector`.
_STUB_QUESTION = "Will the event resolve as expected?"
_STUB_RESOLUTION = "Resolves YES if the event occurs by the cutoff."

# Defensive sanity check: stub strings emitted here must be registered in
# the central detector so downstream gates reject them.
assert _STUB_QUESTION in KNOWN_STUB_PHRASES
assert _STUB_RESOLUTION in KNOWN_STUB_PHRASES

_PROMPT_TMPL = (
    "You are a translator. Convert the following Chinese news event and "
    "analyst notes into a binary-outcome market question following the "
    "Polymarket house style.\n\n"
    "Constraints:\n"
    "* Question must be answerable YES/NO by a clear cutoff date.\n"
    "* Include explicit resolution_criteria.\n"
    "* Tag with 2-4 topic tags.\n\n"
    "TITLE: {title}\nBODY: {body}\nANALYSTS:\n{analyst_notes}\n\n"
    "Return JSON with keys: question_en, resolution_criteria, end_date_iso, tags."
)


def _default_end_date_iso() -> str:
    return (datetime.now(tz=timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_json(text: str) -> dict:
    text = text.strip()
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


async def propose_candidates(
    event: NewsEvent,
    reports: List[AnalystReport],
    llm: LLMCallable,
    *,
    n: int = _CANDIDATE_COUNT,
    model_id: str | None = None,
) -> List[TranslationCandidate]:
    """Generate ``n`` candidate questions in parallel.

    When ``model_id`` is provided, each returned candidate is tagged with a
    ``meta={"model": model_id}`` field so downstream layers (critics,
    moderator, refine) know which LLM produced it without having to plumb
    that information separately.
    """

    analyst_notes = "\n".join(f"- [{r.analyst_id}] {r.summary}" for r in reports)
    prompt = _PROMPT_TMPL.format(
        title=event.title_zh, body=event.body_zh, analyst_notes=analyst_notes
    )

    async def _one(idx: int) -> TranslationCandidate:
        raw = await llm(prompt)
        payload = _extract_json(raw)

        question_en = str(payload.get("question_en") or "").strip()
        resolution_criteria = str(payload.get("resolution_criteria") or "").strip()
        end_date_iso = str(payload.get("end_date_iso") or "").strip()

        # Detect LLM-glitch fallback: empty/unparseable response on either
        # of the two required text fields. We keep the legacy fallback so
        # the pipeline doesn't crash, but flag ``is_stub=True`` so every
        # downstream gate (synthesizer, quality_eval, polymarket client)
        # can decide whether to reject. The warning makes the degradation
        # observable in logs instead of silently dressing it up as a
        # real LLM result.
        is_stub = not question_en or not resolution_criteria
        if is_stub:
            logger.warning(
                "translators: LLM returned empty/unparseable, falling back to stub. "
                "translator_id=t%d event_id=%s title=%r raw=%r",
                idx,
                event.event_id,
                event.title_zh[:80],
                (raw or "")[:200],
            )

        kwargs: dict = dict(
            translator_id=f"t{idx}",
            question_en=question_en or _STUB_QUESTION,
            resolution_criteria=resolution_criteria or _STUB_RESOLUTION,
            end_date_iso=end_date_iso or _default_end_date_iso(),
            tags=list(payload.get("tags") or []),
        )
        meta: dict = {}
        if model_id:
            meta["model"] = model_id
        if is_stub:
            meta["is_stub"] = True
        if meta:
            kwargs["meta"] = meta
        candidate = TranslationCandidate(**kwargs)
        # ``TranslationCandidate`` has ``extra="allow"``, so we can attach
        # the flag as a top-level attribute too. Downstream code may
        # consult either ``candidate.is_stub`` or ``candidate.meta["is_stub"]``.
        if is_stub:
            object.__setattr__(candidate, "is_stub", True)
        return candidate

    return await asyncio.gather(*(_one(i) for i in range(n)))
