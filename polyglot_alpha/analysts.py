"""Analyst stage of the translation pipeline.

The full v2 system runs several specialist analysts (geopolitics, finance,
sports, ...) in parallel over a Chinese news event and emits one
:class:`AnalystReport` per analyst. The agents-package only needs a thin
async surface to chain into the translator stage; the heavyweight prompt
templates and reranking will land in a follow-up task.

The single ``run_analysts`` entrypoint is the contract every downstream
caller (``synthesizer``, ``BaseTranslatorAgent.run_pipeline``) relies on.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import List, Tuple

from .llm import LLMCallable
from .schemas import AnalystReport, NewsEvent

logger = logging.getLogger(__name__)


_ANALYSTS: Tuple[Tuple[str, str], ...] = (
    ("geopolitics", "Identify state actors, treaties, sanctions, regional implications."),
    ("finance", "Identify markets, instruments, monetary or fiscal policy moves, FX impact."),
    ("tech", "Identify companies, products, regulators, supply-chain or platform risk."),
)

_PROMPT_TMPL = (
    "You are a {role} analyst. Read the Chinese-language event below and "
    "produce a one-paragraph English summary, then a JSON object with keys "
    "'entities' (list of strings) and 'risks' (list of strings).\n\n"
    "Focus: {focus}\n\n"
    "TITLE: {title}\nBODY: {body}\n\n"
    "Respond with: SUMMARY: <text>\\nJSON: <json>"
)


def _parse_response(text: str) -> Tuple[str, List[str], List[str]]:
    """Parse a structured analyst response.

    Fail mode: when the LLM returns no ``JSON:`` marker or a tail that
    fails to parse, we return ``entities=[], risks=[]`` and a summary
    equal to the raw text. This is treated downstream as a low-signal
    result (LLM glitch). The warning below makes the degradation
    observable in logs instead of being silently absorbed.
    """

    summary = text
    entities: List[str] = []
    risks: List[str] = []
    if "JSON:" in text:
        head, _, tail = text.partition("JSON:")
        summary = head.replace("SUMMARY:", "").strip()
        try:
            payload = json.loads(tail.strip())
            entities = list(payload.get("entities") or [])
            risks = list(payload.get("risks") or [])
        except json.JSONDecodeError:
            logger.warning(
                "analysts: JSON parse failed for response (first 200 chars): %s",
                (text or "")[:200],
            )
    else:
        # Missing ``JSON:`` marker entirely — either empty LLM output or
        # a free-form response that ignored the protocol. Either way the
        # entities/risks list will be empty, so downstream should treat
        # this as low-signal.
        logger.warning(
            "analysts: response missing 'JSON:' marker (first 200 chars): %s",
            (text or "")[:200],
        )
    return summary, entities, risks


async def run_analysts(event: NewsEvent, llm: LLMCallable) -> List[AnalystReport]:
    """Run all configured analysts concurrently. Returns one report each."""

    async def _one(analyst_id: str, focus: str) -> AnalystReport:
        prompt = _PROMPT_TMPL.format(
            role=analyst_id, focus=focus, title=event.title_zh, body=event.body_zh
        )
        raw = await llm(prompt)
        summary, entities, risks = _parse_response(raw)
        return AnalystReport(
            analyst_id=analyst_id,
            summary=summary,
            relevant_entities=entities,
            risk_factors=risks,
        )

    return await asyncio.gather(*(_one(aid, foc) for aid, foc in _ANALYSTS))
