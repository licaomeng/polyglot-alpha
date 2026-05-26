"""Translator stage of the pipeline.

Takes the analyst reports + raw event and emits one or more candidate
Polymarket-shaped market questions. The agent code calls
``propose_candidates`` and forwards the candidates to ``synthesizer``.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import List

from .llm import LLMCallable
from .schemas import AnalystReport, NewsEvent, TranslationCandidate


_CANDIDATE_COUNT = 2

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
        kwargs: dict = dict(
            translator_id=f"t{idx}",
            question_en=str(payload.get("question_en") or "").strip()
            or "Will the event resolve as expected?",
            resolution_criteria=str(payload.get("resolution_criteria") or "").strip()
            or "Resolves YES if the event occurs by the cutoff.",
            end_date_iso=str(payload.get("end_date_iso") or "").strip()
            or _default_end_date_iso(),
            tags=list(payload.get("tags") or []),
        )
        if model_id:
            kwargs["meta"] = {"model": model_id}
        return TranslationCandidate(**kwargs)

    return await asyncio.gather(*(_one(i) for i in range(n)))
