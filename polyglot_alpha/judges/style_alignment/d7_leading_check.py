"""D7 - Leading-bias check: no nudging language in the framing."""

from __future__ import annotations

import re
from typing import Optional

from polyglot_alpha.judges.style_alignment.llm_batch import (
    LlmCall,
    run_style_llm_batch,
)
from polyglot_alpha.judges.types import JudgeResult, PanelQuestion

JUDGE_NAME = "d7_leading_check"

# Words that almost always indicate editorial / leading framing.
_LEADING_TERMS = re.compile(
    r"\b(?:obviously|clearly|surely|definitely|undoubtedly|"
    r"shocking|amazing|disastrous|catastrophic|inevitable|"
    r"finally|at last|of course)\b",
    re.IGNORECASE,
)


async def judge_d7_leading_check(
    question: PanelQuestion,
    llm_call: Optional[LlmCall] = None,
) -> JudgeResult:
    batch = await run_style_llm_batch(question, llm_call=llm_call)
    entry = batch.get("d7", {})

    leading_hits = _LEADING_TERMS.findall(question.title)
    deterministic_fail = bool(leading_hits)

    passed = bool(entry.get("passed", False)) and not deterministic_fail
    score = 0.0 if deterministic_fail else float(entry.get("score", 0.0))
    reason_bits: list[str] = []
    if deterministic_fail:
        reason_bits.append(
            f"leading-bias term(s) detected: {sorted(set(leading_hits))}"
        )
    if entry.get("reason"):
        reason_bits.append(str(entry["reason"]))

    return JudgeResult(
        name=JUDGE_NAME,
        passed=passed,
        score=score,
        reason="; ".join(reason_bits) or "No leading bias detected.",
        evidence={
            "raw": entry,
            "leading_hits": leading_hits,
            "offline": batch.get("offline", False),
        },
    )
