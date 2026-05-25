"""D4 - Granularity check: single resolvable question (no compound clauses).

Deterministic: we look for compound markers (and/or with a verb on each
side, multiple question marks, comma-separated clauses) that would
require resolving more than one fact. This is a *hard* requirement for
the panel verdict.
"""

from __future__ import annotations

import re

from polyglot_alpha.judges.types import JudgeResult, PanelQuestion

JUDGE_NAME = "d4_granularity"

# Tokens that, in a question title, typically indicate two predicates.
_COMPOUND_TOKENS = re.compile(
    r"\b(?:and|or)\b\s+(?:will|did|has|have|is|are|the|a|an)\b",
    re.IGNORECASE,
)
_MULTI_Q = re.compile(r"\?\s*\S+\s*\?")
# Three or more 'and'/'or' connectors in a title is a strong signal of
# enumeration of multiple resolvable events (vs. a single rule body).
_MANY_CONNECTORS = re.compile(r"\b(?:and|or)\b", re.IGNORECASE)


async def judge_d4_granularity(question: PanelQuestion) -> JudgeResult:
    title = question.title.strip()
    if not title:
        return JudgeResult(
            name=JUDGE_NAME,
            passed=False,
            score=0.0,
            reason="Empty title.",
            evidence={"compound_match": None},
        )

    multi_q = bool(_MULTI_Q.search(title))
    compound = _COMPOUND_TOKENS.search(title)
    connector_count = len(_MANY_CONNECTORS.findall(title))
    many_connectors = connector_count >= 2

    if multi_q or compound or many_connectors:
        reasons = []
        if compound:
            reasons.append("compound predicate detected")
        if many_connectors:
            reasons.append(f"{connector_count} connectors (and/or) — likely enumeration")
        if multi_q:
            reasons.append("multiple question marks")
        return JudgeResult(
            name=JUDGE_NAME,
            passed=False,
            score=0.0,
            reason="; ".join(reasons) + " — split into independent markets.",
            evidence={
                "compound_match": compound.group(0) if compound else None,
                "multi_question": multi_q,
                "connector_count": connector_count,
                "title": title,
            },
        )

    return JudgeResult(
        name=JUDGE_NAME,
        passed=True,
        score=1.0,
        reason="Single resolvable predicate.",
        evidence={"title": title},
    )
