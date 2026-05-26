"""D1 - Structural pattern check against the 6 canonical Polymarket templates.

Actual distribution (T5 corpus scan, n=5000, see ``corpus/patterns_report.md``):

  * P1 "Will X by [date]?"                        85.6%   (was estimated 45%)
  * P2 Noun-phrase multi-outcome                   5.9%
  * P3 "[Asset] above ___"                         7.2%
  * P4 "Who will be the next X?"                   0.0%
  * P5 "Will X happen between [start] and [end]?"  1.1%
  * P6 "How many X by [date]?"                     0.0%

D1 preferentially accepts P1: a regex hit on the high-prior pattern is
treated as full confidence, while rare patterns score lower unless
explicitly confident. This guards against the synthesizer dropping into
low-corpus-frequency forms when P1 was the safer choice.

We match with regex first (cheap, deterministic, confidence 0.95+). If
no regex hits, the judge falls back to an LLM call that judges shape on
a higher level — useful for unusual phrasings that humans would accept
but the regex grid misses. The LLM fallback returns lower confidence
(0.6) than regex hits so downstream weighting can discount it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from polyglot_alpha.corpus.few_shots_extended import get_exemplars_for_dimension
from polyglot_alpha.judges.style_alignment.llm_batch import (
    _call_default_backend,
    _log_llm_call,
)
from polyglot_alpha.judges.types import JudgeResult, PanelQuestion

LlmCall = Callable[[str], Awaitable[str]]

JUDGE_NAME = "d1_structural"


# Corpus-derived priors (T5 scan of 5K live Polymarket markets, May 2026).
# Keys mirror the canonical P1-P6 label scheme from README §5.21.
PATTERN_PRIORS: dict[str, float] = {
    "P1_will_by_date": 0.856,
    "P2_noun_phrase": 0.059,
    "P3_threshold": 0.072,
    "P4_who_next": 0.000,
    "P5_between_dates": 0.011,
    "P6_how_many": 0.000,
}
assert abs(sum(PATTERN_PRIORS.values()) - 1.0) < 0.05, (
    "PATTERN_PRIORS should sum to ~1.0 (small unclassified residual allowed)."
)

# Map internal regex pattern names to canonical P-labels for prior lookup.
_PATTERN_TO_PRIOR_KEY: dict[str, str] = {
    "will_x_by_date": "P1_will_by_date",
    "will_x_no_date": "P1_will_by_date",  # bare 'Will X?' still maps to P1.
    "asset_above_threshold": "P3_threshold",
    "will_x_between_dates": "P5_between_dates",
    "how_many_x_by_date": "P6_how_many",
    "who_next_x": "P4_who_next",
}

# A pattern is "rare" if its corpus prior falls below this fraction.
RARE_PATTERN_PRIOR_THRESHOLD = 0.05


@dataclass(frozen=True)
class _Pattern:
    name: str
    regex: re.Pattern[str]
    weight: float  # informational, not used in the gate


# Date fragment: matches "by August 23, 2026" / "by 2026-08-31" / "before 2026"
_DATE_RE = (
    r"(?:by|before|on|until)\s+"
    r"(?:[A-Z][a-z]+\s+\d{1,2},?\s*\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{4})"
)

_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        name="will_x_by_date",
        regex=re.compile(rf"^\s*Will\s+.+?\s+{_DATE_RE}\s*\??\s*$", re.IGNORECASE),
        weight=0.45,
    ),
    _Pattern(
        name="will_x_between_dates",
        regex=re.compile(
            r"^\s*Will\s+.+?\s+between\s+.+?\s+and\s+.+?\??\s*$", re.IGNORECASE
        ),
        weight=0.10,
    ),
    _Pattern(
        name="who_next_x",
        regex=re.compile(r"^\s*Who\s+will\s+(?:be|become)\s+the\s+next\s+.+?\??\s*$", re.IGNORECASE),
        weight=0.10,
    ),
    _Pattern(
        name="how_many_x_by_date",
        regex=re.compile(rf"^\s*How\s+many\s+.+?\s+{_DATE_RE}\s*\??\s*$", re.IGNORECASE),
        weight=0.05,
    ),
    _Pattern(
        name="asset_above_threshold",
        regex=re.compile(
            r"^\s*(?:Will\s+)?[A-Z][\w./ ]+\s+(?:above|over|exceed|reach)\s+\$?\d", re.IGNORECASE
        ),
        weight=0.15,
    ),
    _Pattern(
        name="will_x_no_date",  # bare "Will X?" - still a Will pattern, no explicit date
        regex=re.compile(r"^\s*Will\s+.+?\??\s*$", re.IGNORECASE),
        weight=0.0,  # only used as a soft match
    ),
)

DEFAULT_PATTERN_NAMES: tuple[str, ...] = tuple(p.name for p in _PATTERNS if p.weight > 0)


# Confidence levels for regex hits vs the LLM fallback. Regex matches are
# corpus-derived and deterministic so we trust them at ~0.95+. The LLM
# fallback is fuzzy and may hallucinate, so we cap it at 0.6 (still PASS
# but downstream callers can see the lower confidence in evidence).
REGEX_HIT_CONFIDENCE: float = 0.95
LLM_FALLBACK_CONFIDENCE: float = 0.6


def _build_llm_fallback_prompt(title: str) -> str:
    """Build the LLM-fallback prompt anchored on D1 exemplars."""

    exemplars = get_exemplars_for_dimension("D1")
    exemplar_lines: list[str] = []
    for ex in exemplars:
        role = ex["role"]
        text = ex["text"]
        exemplar_lines.append(f"- [{role}] {text}")
    exemplar_block = "\n".join(exemplar_lines) if exemplar_lines else "(none)"

    return (
        "You are an editor evaluating whether a candidate market title"
        " structurally resembles a Polymarket question. Canonical"
        " Polymarket questions are PREDICTIVE, BINARY (or close-set),"
        " carry a clear actor/event/threshold and an implicit or"
        " explicit deadline. Declaratives, advice, and bare assertions"
        " are NOT Polymarket questions.\n\n"
        f"EXEMPLARS:\n{exemplar_block}\n\n"
        f"CANDIDATE TITLE: {title}\n\n"
        "Respond with ONLY a JSON object: "
        '{"is_polymarket_shape": true|false, "reason": "..."}'
    )


def _parse_llm_fallback(raw: str) -> Optional[dict[str, object]]:
    """Parse the LLM fallback response. Returns None if unparseable."""

    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None
    if not isinstance(data, dict):
        return None
    if "is_polymarket_shape" not in data:
        return None
    return {
        "is_polymarket_shape": bool(data.get("is_polymarket_shape", False)),
        "reason": str(data.get("reason", "") or ""),
    }


async def _run_llm_fallback(
    title: str, llm_call: Optional[LlmCall]
) -> Optional[dict[str, object]]:
    """Run the LLM fallback. Returns None when offline / unparseable."""

    backend = llm_call
    if backend is None:
        if not (
            os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        ):
            return None

    prompt = _build_llm_fallback_prompt(title)
    provider = "injected" if backend is not None else "fallback:default"
    try:
        if backend is not None:
            raw = await backend(prompt)
        else:
            # Reuse the D2 default route (DeepSeek) — D1 has no dedicated
            # provider mapping but DeepSeek is the cheapest tier.
            raw = await _call_default_backend(prompt, "d2")
        _log_llm_call(
            JUDGE_NAME, provider, len(prompt), len(raw or ""), success=True
        )
    except Exception as exc:  # noqa: BLE001
        _log_llm_call(
            JUDGE_NAME, provider, len(prompt), 0, success=False, error=str(exc)
        )
        return None

    return _parse_llm_fallback(raw or "")


async def judge_d1_structural(
    question: PanelQuestion,
    ground_truth_patterns: list[str] | None = None,
    *,
    enable_llm_fallback: bool = True,
    llm_call: Optional[LlmCall] = None,
) -> JudgeResult:
    """Return pass if the title matches one of the canonical patterns.

    Args:
        question: Panel question to judge.
        ground_truth_patterns: Optional restricted set of pattern names.
        enable_llm_fallback: When ``False``, skip the LLM fallback
            entirely. Defaults to ``True`` but the fallback is a no-op
            unless a backend is reachable.
        llm_call: Optional injected LLM backend (test override).
    """

    allowed = set(ground_truth_patterns or DEFAULT_PATTERN_NAMES)
    title = question.title.strip()

    if not title:
        return JudgeResult(
            name=JUDGE_NAME,
            passed=False,
            score=0.0,
            reason="Empty title.",
            evidence={"matched_pattern": None, "allowed": sorted(allowed)},
        )

    matched: list[str] = []
    for pat in _PATTERNS:
        if pat.name not in allowed and pat.weight > 0:
            continue
        if pat.regex.match(title):
            matched.append(pat.name)

    if matched:
        # Prefer the highest corpus-prior match (P1 dominates with 85.6%).
        def _prior_for(name: str) -> float:
            return PATTERN_PRIORS.get(_PATTERN_TO_PRIOR_KEY.get(name, ""), 0.0)

        best = max(matched, key=_prior_for)
        best_prior = _prior_for(best)
        # High-prior patterns (P1) get full score; rare patterns get a
        # discount because the synthesizer dropping into them is suspicious
        # unless explicitly justified (which we cannot verify without an
        # LLM tier — that is fine; rare-pattern questions still PASS the
        # gate, just at a lower confidence).
        if best_prior >= RARE_PATTERN_PRIOR_THRESHOLD:
            score = 1.0
            reason_tail = " (corpus-frequent pattern)"
        else:
            score = 0.75
            reason_tail = (
                f" (rare pattern, corpus prior={best_prior:.3f}; downscored)"
            )
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,
            score=score,
            reason=f"Title matches canonical pattern '{best}'.{reason_tail}",
            evidence={
                "matched_pattern": best,
                "matched_prior_key": _PATTERN_TO_PRIOR_KEY.get(best),
                "matched_prior": best_prior,
                "all_matches": matched,
                "title": title,
                "rare_pattern": best_prior < RARE_PATTERN_PRIOR_THRESHOLD,
                "tier": "regex",
                "confidence": REGEX_HIT_CONFIDENCE,
            },
        )

    # --- LLM fallback for unusual phrasings the regex grid misses --------- #
    if enable_llm_fallback:
        fallback = await _run_llm_fallback(title, llm_call)
        if fallback is not None:
            if fallback["is_polymarket_shape"]:
                return JudgeResult(
                    name=JUDGE_NAME,
                    passed=True,
                    score=LLM_FALLBACK_CONFIDENCE,
                    reason=(
                        "LLM fallback accepts as Polymarket-shape: "
                        f"{fallback.get('reason') or '(no reason)'}"
                    ),
                    evidence={
                        "matched_pattern": None,
                        "title": title,
                        "tier": "llm_fallback",
                        "confidence": LLM_FALLBACK_CONFIDENCE,
                        "llm_reason": fallback.get("reason"),
                    },
                )
            # LLM agrees the shape is not Polymarket — keep FAIL but
            # record the LLM evidence so we don't re-run it elsewhere.
            return JudgeResult(
                name=JUDGE_NAME,
                passed=False,
                score=0.0,
                reason=(
                    "No regex match; LLM fallback also rejects shape: "
                    f"{fallback.get('reason') or '(no reason)'}"
                ),
                evidence={
                    "matched_pattern": None,
                    "allowed": sorted(allowed),
                    "title": title,
                    "tier": "llm_fallback",
                    "confidence": LLM_FALLBACK_CONFIDENCE,
                    "llm_reason": fallback.get("reason"),
                },
            )

    return JudgeResult(
        name=JUDGE_NAME,
        passed=False,
        score=0.0,
        reason="Title does not match any canonical Polymarket pattern.",
        evidence={
            "matched_pattern": None,
            "allowed": sorted(allowed),
            "title": title,
            "tier": "regex",
        },
    )
