"""D3 - Framing check: predictive (uncertain future) vs declarative (fact)."""

from __future__ import annotations

from typing import Optional

from polyglot_alpha.judges.style_alignment.llm_batch import (
    LlmCall,
    run_style_llm_batch,
)
from polyglot_alpha.judges.types import JudgeResult, PanelQuestion

JUDGE_NAME = "d3_framing"


async def judge_d3_framing(
    question: PanelQuestion,
    llm_call: Optional[LlmCall] = None,
) -> JudgeResult:
    batch = await run_style_llm_batch(question, llm_call=llm_call)
    entry = batch.get("d3", {})
    return JudgeResult(
        name=JUDGE_NAME,
        passed=bool(entry.get("passed", False)),
        score=float(entry.get("score", 0.0)),
        reason=str(entry.get("reason", "")) or "Framing judged via shared LLM batch.",
        evidence={"raw": entry, "offline": batch.get("offline", False)},
    )
