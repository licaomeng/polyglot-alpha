"""D6 - Source-reliability check: cited sources match content."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from polyglot_alpha.judges.style_alignment.llm_batch import (
    LlmCall,
    run_style_llm_batch,
)
from polyglot_alpha.judges.types import JudgeResult, PanelQuestion

JUDGE_NAME = "d6_source_reliability"

# Lightweight allowlist — official government / regulator / major news.
# We use this as a *positive* prior; the LLM batch result is the final say.
_AUTHORITATIVE_TLDS = (".gov.cn", ".gov", ".gov.uk", ".gob.mx", ".go.jp")
_AUTHORITATIVE_HOSTS = (
    "pbc.gov.cn",
    "mof.gov.cn",
    "stats.gov.cn",
    "csrc.gov.cn",
    "xinhuanet.com",
    "reuters.com",
    "bloomberg.com",
)


def _has_authoritative_host(url: str) -> bool:
    if not url:
        return False
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if any(host.endswith(tld) for tld in _AUTHORITATIVE_TLDS):
        return True
    return host in _AUTHORITATIVE_HOSTS


async def judge_d6_source_reliability(
    question: PanelQuestion,
    llm_call: Optional[LlmCall] = None,
) -> JudgeResult:
    batch = await run_style_llm_batch(question, llm_call=llm_call)
    entry = batch.get("d6", {})
    authoritative = _has_authoritative_host(question.resolution_source)
    # LLM passes OR authoritative URL — either is sufficient.
    passed = bool(entry.get("passed", False)) or authoritative
    score = max(float(entry.get("score", 0.0)), 1.0 if authoritative else 0.0)
    reason_bits: list[str] = []
    if authoritative:
        reason_bits.append("authoritative resolution_source URL detected")
    if entry.get("reason"):
        reason_bits.append(str(entry["reason"]))
    return JudgeResult(
        name=JUDGE_NAME,
        passed=passed,
        score=score,
        reason="; ".join(reason_bits) or "Source reliability judged via LLM batch.",
        evidence={
            "raw": entry,
            "authoritative_host": authoritative,
            "resolution_source": question.resolution_source,
            "offline": batch.get("offline", False),
        },
    )
