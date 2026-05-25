"""D5 - Resolution-clarity check (highest-EV style judge per README §5.22).

A market is only tradeable if the resolution date and the resolution
criteria are both explicit and machine-checkable. The judge has two tiers:

  * **Fast path (rule-based).** Catches the obvious failure modes:
        - ``cutoff_ts`` parses as ISO-8601 (or close enough);
        - ``resolution_criteria`` is non-empty AND contains a YES / NO axis
          or enough text to describe a rule.
    A title with no date keyword at all and no parseable ``cutoff_ts`` is
    an instant FAIL — there's nothing for the LLM tier to rescue.

  * **Slow path (LLM tier).** When the fast path passes the structural
    checks but the resolution criteria text might still hide an ambiguity,
    we ask the LLM (DeepSeek/OpenRouter per ``llm_batch.PROVIDER_FOR_DIMENSION``)
    to enumerate any ambiguities that could plausibly trigger an UMA
    dispute. The prompt is anchored on the D5 exemplars from
    ``few_shots_extended.EXTENDED_EXEMPLARS`` (Fix 2). If the model
    returns ``NONE`` we keep the PASS; if it returns >=1 ambiguity we
    flip to FAIL with the ambiguity list in ``evidence``.

The LLM tier only fires when ``enable_llm=True`` (default) AND an
``llm_call`` is injected OR a backend key is reachable. Offline / no-key
runs short-circuit to the rule-based verdict — important so unit tests
that don't mock the LLM keep passing.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Awaitable, Callable, Optional

from polyglot_alpha.corpus.few_shots_extended import get_exemplars_for_dimension
from polyglot_alpha.judges.style_alignment.llm_batch import (
    PROVIDER_FOR_DIMENSION,
    _call_default_backend,
    _log_llm_call,
)
from polyglot_alpha.judges.types import JudgeResult, PanelQuestion

JUDGE_NAME = "d5_resolution_clarity"

LlmCall = Callable[[str], Awaitable[str]]

_YES_NO_TOKENS = re.compile(r"\b(?:YES|NO|yes|no)\b")
_DATE_HINTS = re.compile(
    r"(?:by|before|on|until)\s+\d|"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
    r"[A-Z][a-z]+\s+\d{1,2},?\s*\d{4}"
)

# A criterion body shorter than this AND lacking a YES/NO axis is treated
# as insufficient by the rule-based tier.
_MIN_CRITERIA_LEN = 40


def _parse_cutoff(raw: str) -> bool:
    if not raw:
        return False
    candidate = raw.strip()
    # Accept "Z" suffix as +00:00 for Python <3.11 compatibility.
    candidate = candidate.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
        return True
    except ValueError:
        return False


def _build_llm_prompt(question: PanelQuestion) -> str:
    """Build the LLM-tier D5 prompt, anchored on D5 exemplars."""

    exemplars = get_exemplars_for_dimension("D5")
    exemplar_lines: list[str] = []
    for ex in exemplars:
        role = ex["role"]
        text = ex["text"]
        rationale = ex["rationale"]
        exemplar_lines.append(f"- [{role}] {text}\n    rationale: {rationale}")
    exemplar_block = "\n".join(exemplar_lines) if exemplar_lines else "(none)"

    return (
        "You are a Polymarket resolution-clarity auditor. Given the question"
        " below, identify ANY ambiguity that could plausibly cause an UMA"
        " dispute. Examples of ambiguity: missing source URL, undefined"
        " threshold, unclear actor, missing time zone, subjective"
        " adjective ('big', 'soon'), undefined event ('a deal').\n\n"
        f"EXEMPLARS (positive=clear, negative=ambiguous):\n{exemplar_block}\n\n"
        "QUESTION TO AUDIT:\n"
        f"TITLE: {question.title or '(empty)'}\n"
        f"DESCRIPTION: {question.description or '(none)'}\n"
        f"RESOLUTION_CRITERIA: {question.resolution_criteria or '(none)'}\n"
        f"RESOLUTION_SOURCE: {question.resolution_source or '(none)'}\n"
        f"CUTOFF_TS: {question.cutoff_ts or '(none)'}\n\n"
        "Respond with ONLY a JSON object:\n"
        '{"ambiguities": ["...", "..."]}\n'
        "If there are NO ambiguities, return:\n"
        '{"ambiguities": []}\n'
        "Do not include any prose outside the JSON object."
    )


def _parse_llm_ambiguities(raw: str) -> Optional[list[str]]:
    """Parse the LLM response into a list of ambiguity strings.

    Returns ``None`` if the payload is unparseable (caller should treat
    as ``no signal`` and keep the rule-based verdict).
    """

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
    ambiguities = data.get("ambiguities")
    if ambiguities is None:
        # Tolerate "NONE" / "[]" string responses.
        return []
    if isinstance(ambiguities, str):
        upper = ambiguities.strip().upper()
        if upper in {"NONE", "[]", "NULL", ""}:
            return []
        return [ambiguities.strip()]
    if not isinstance(ambiguities, list):
        return None
    return [str(item).strip() for item in ambiguities if str(item).strip()]


async def judge_d5_resolution_clarity(
    question: PanelQuestion,
    *,
    enable_llm: bool = True,
    llm_call: Optional[LlmCall] = None,
) -> JudgeResult:
    """Audit resolution clarity with a rule-based fast path + LLM slow path.

    Args:
        question: The panel question to audit.
        enable_llm: When ``False``, skip the LLM tier entirely. Useful for
            unit tests that don't want to mock the LLM at all.
        llm_call: Optional injected LLM backend (test override).
    """

    # --- Fast path (rule-based, identical to v1 D5) ----------------------- #
    issues: list[str] = []
    cutoff_ok = _parse_cutoff(question.cutoff_ts)
    if not cutoff_ok and not _DATE_HINTS.search(question.title):
        issues.append("no parseable cutoff_ts and no date hint in title")

    criteria = question.resolution_criteria.strip()
    if not criteria:
        issues.append("resolution_criteria is empty")
    elif not (_YES_NO_TOKENS.search(criteria) or len(criteria) > _MIN_CRITERIA_LEN):
        issues.append("resolution_criteria lacks YES/NO axis or rule body")

    if issues:
        # Instant FAIL — LLM tier cannot rescue a missing date / missing
        # criteria. This is the "fast path fail" case.
        return JudgeResult(
            name=JUDGE_NAME,
            passed=False,
            score=0.0,
            reason="; ".join(issues),
            evidence={
                "cutoff_parsed": cutoff_ok,
                "criteria_len": len(criteria),
                "issues": issues,
                "tier": "rule",
            },
        )

    rule_evidence = {
        "cutoff_parsed": cutoff_ok,
        "criteria_len": len(criteria),
        "tier": "rule",
    }

    # --- Slow path (LLM tier) -------------------------------------------- #
    # We only consult the LLM when explicitly enabled AND a backend is
    # reachable (either via injection or via an env-var-configured
    # provider). Anything else short-circuits to a rule-only PASS so unit
    # tests that don't mock the LLM remain green.
    if not enable_llm:
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,
            score=1.0,
            reason="Cutoff date and resolution criteria are both explicit.",
            evidence=rule_evidence,
        )

    backend = llm_call
    provider = "injected" if backend is not None else PROVIDER_FOR_DIMENSION.get(
        "d5", "fallback:gemini"
    )

    if backend is None:
        # No injected stub — only attempt the real backend if a key is set.
        import os

        if not (
            os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        ):
            # Offline: keep the rule-based PASS.
            return JudgeResult(
                name=JUDGE_NAME,
                passed=True,
                score=1.0,
                reason=(
                    "Cutoff date and resolution criteria are both explicit"
                    " (LLM tier skipped: no backend key)."
                ),
                evidence={**rule_evidence, "llm_tier": "skipped_no_key"},
            )

    prompt = _build_llm_prompt(question)
    try:
        if backend is not None:
            raw = await backend(prompt)
        else:
            raw = await _call_default_backend(prompt, "d5")
        _log_llm_call(
            JUDGE_NAME, provider, len(prompt), len(raw or ""), success=True
        )
    except Exception as exc:  # noqa: BLE001
        _log_llm_call(
            JUDGE_NAME, provider, len(prompt), 0, success=False, error=str(exc)
        )
        # Offline / failure — fall back to the rule-based PASS.
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,
            score=1.0,
            reason=(
                "Cutoff date and resolution criteria are both explicit"
                f" (LLM tier unavailable: {exc})."
            ),
            evidence={**rule_evidence, "llm_tier": "error", "llm_error": str(exc)},
        )

    ambiguities = _parse_llm_ambiguities(raw or "")
    if ambiguities is None:
        # Unparseable LLM response — keep rule-based PASS but note it.
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,
            score=1.0,
            reason=(
                "Cutoff date and resolution criteria are both explicit"
                " (LLM tier returned unparseable output; rule verdict kept)."
            ),
            evidence={
                **rule_evidence,
                "llm_tier": "unparseable",
                "llm_raw": (raw or "")[:200],
                "provider": provider,
            },
        )

    if not ambiguities:
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,
            score=1.0,
            reason=(
                "Cutoff date and resolution criteria are both explicit;"
                " LLM tier found no ambiguities."
            ),
            evidence={
                **rule_evidence,
                "llm_tier": "passed",
                "ambiguities": [],
                "provider": provider,
            },
        )

    # LLM found ambiguities — flip to FAIL.
    return JudgeResult(
        name=JUDGE_NAME,
        passed=False,
        score=0.0,
        reason=(
            f"LLM tier flagged {len(ambiguities)} resolution ambiguit"
            f"{'y' if len(ambiguities) == 1 else 'ies'}: "
            + "; ".join(ambiguities[:3])
        ),
        evidence={
            **rule_evidence,
            "tier": "llm",
            "llm_tier": "failed",
            "ambiguities": ambiguities,
            "provider": provider,
        },
    )
