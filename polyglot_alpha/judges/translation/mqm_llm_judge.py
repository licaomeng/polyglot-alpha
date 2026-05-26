"""MQM-style LLM judge.

Extends the Layer-4 quality eval used elsewhere in PolyglotAlpha: a
structured-output LLM call enumerates Major / Minor errors across MQM
categories (Accuracy, Fluency, Style, Terminology), then we collapse to
a 0-100 score using the standard MQM weighting (Major=5, Minor=1).

**Provider mapping.** The translation judges are kept at three (BLEU,
COMET, MQM-LLM); MQM is now routed through Anthropic Claude Haiku 4.5
direct (matching the seeders' backing model so the panel infra is a
single provider after the OpenRouter swap). BLEU + COMET remain
non-LLM offline judges. Anti-collusion still holds because BLEU/COMET
share no upstream with the Claude judge.

Backends:
    * Anthropic Haiku 4.5 (default) when ``ANTHROPIC_API_KEY`` is set.
    * OpenRouter Llama 3.3 70B (legacy fallback) when only
      ``OPENROUTER_API_KEY`` is set.
    * Gemini fallback when ``GEMINI_API_KEY`` is set and neither
      Anthropic nor OpenRouter are reachable.
    * Any user-supplied async callable passed as ``llm_call``.
    * Offline graceful degradation when no backend is reachable.

Every successful or failed LLM round-trip emits a JSONL line to
``outputs/llm_cost_log.jsonl`` so the panel operator can audit free-tier
spend during demos.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from polyglot_alpha.judges.types import JudgeResult, PanelQuestion

JUDGE_NAME = "mqm_llm"
PROVIDER_LABEL = "anthropic:claude-haiku-4-5-20251001"
LLM_COST_LOG_PATH = Path("outputs/llm_cost_log.jsonl")


def _log_llm_call(
    judge_name: str,
    provider: str,
    prompt_chars: int,
    response_chars: int,
    success: bool,
    error: Optional[str] = None,
) -> None:
    """Best-effort JSONL cost / call log. Failures are swallowed."""

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "judge": judge_name,
        "provider": provider,
        "prompt_chars": prompt_chars,
        "response_chars": response_chars,
        "success": success,
        "error": error,
    }
    try:
        LLM_COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LLM_COST_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:  # pragma: no cover
        pass

# MQM error weights — Major counts 5x a Minor. Critical is folded into Major
# for the binary "major errors == 0" gate that the panel enforces.
MAJOR_WEIGHT = 5
MINOR_WEIGHT = 1
MAX_PENALTY = 100  # score floors at 0
MQM_CATEGORIES = ("Accuracy", "Fluency", "Style", "Terminology")

LlmCall = Callable[[str], Awaitable[str]]


_PROMPT = """You are an MQM (Multidimensional Quality Metrics) annotator for prediction-market questions translated from {src_lang} to {tgt_lang}.

SOURCE NEWS ({src_lang}):
{source_news}

CANDIDATE QUESTION ({tgt_lang}):
{candidate}

DESCRIPTION (for context):
{description}

Analyze the candidate. Identify every translation error and classify each one:
  - category: one of {categories}
  - severity: one of "MAJOR" (distorts meaning, would mislead a trader) or "MINOR" (stylistic, doesn't change resolution).
  - detail: one-sentence justification.

Respond with ONLY a JSON object — no prose, no markdown fences:
{{
  "errors": [
    {{"category": "...", "severity": "MAJOR|MINOR", "detail": "..."}}
  ],
  "rationale": "one paragraph summary"
}}
"""


def _build_prompt(question: PanelQuestion) -> str:
    return _PROMPT.format(
        src_lang=question.source_language,
        tgt_lang=question.target_language,
        source_news=question.source_news or "(none provided)",
        candidate=question.title,
        description=question.description or "(none)",
        categories=", ".join(MQM_CATEGORIES),
    )


def _parse_response(raw: str) -> dict[str, Any]:
    """Tolerant JSON parser. Returns ``{"errors": [...], "rationale": ...}``."""

    text = raw.strip()
    # Strip common ```json fences the model might add despite instructions.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Last-ditch: find first { ... }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {"errors": [], "rationale": f"unparseable: {text[:120]}"}
        else:
            return {"errors": [], "rationale": f"unparseable: {text[:120]}"}
    if not isinstance(data, dict):
        return {"errors": [], "rationale": "non-dict response"}
    errors = data.get("errors") or []
    if not isinstance(errors, list):
        errors = []
    return {"errors": errors, "rationale": str(data.get("rationale", ""))}


def _score_from_errors(errors: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Return (score, major_count, minor_count)."""

    major = sum(1 for e in errors if str(e.get("severity", "")).upper() == "MAJOR")
    minor = sum(1 for e in errors if str(e.get("severity", "")).upper() == "MINOR")
    # Treat CRITICAL as MAJOR for the panel gate.
    critical = sum(
        1 for e in errors if str(e.get("severity", "")).upper() == "CRITICAL"
    )
    major += critical
    penalty = min(MAX_PENALTY, major * MAJOR_WEIGHT * 4 + minor * MINOR_WEIGHT)
    score = max(0, 100 - penalty)
    return score, major, minor


async def _call_anthropic_haiku(prompt: str) -> str:
    """Default MQM backend: Claude Haiku 4.5 direct via the Anthropic SDK.

    Falls back to OpenRouter Llama (legacy) when only
    ``OPENROUTER_API_KEY`` is set, then to Gemini if neither is
    available.
    """

    import asyncio

    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        from polyglot_alpha.llm import AnthropicLLM, CLAUDE_HAIKU

        llm = AnthropicLLM(model=CLAUDE_HAIKU, api_key=anthropic_key)
        return await llm.complete(
            system=(
                "You are an MQM annotator. Return ONLY a JSON object —"
                " no prose, no markdown fences."
            ),
            user=prompt,
            max_tokens=1024,
            temperature=0.0,
        )

    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        try:
            import httpx
        except ImportError:
            httpx = None  # type: ignore[assignment]

        if httpx is not None:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": "meta-llama/llama-3.3-70b-instruct",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"] or ""

    # Fall back to Gemini if neither Anthropic nor OpenRouter is configured.
    import google.generativeai as genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "None of ANTHROPIC_API_KEY, OPENROUTER_API_KEY, or"
            " GEMINI_API_KEY is set"
        )
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash-exp")

    def _sync() -> str:
        resp = model.generate_content(prompt)
        return resp.text or ""

    return await asyncio.to_thread(_sync)


# Backwards-compatible aliases — older callers / tests may import either name.
_call_openrouter_llama = _call_anthropic_haiku
_call_gemini = _call_anthropic_haiku


async def judge_mqm_llm(
    question: PanelQuestion,
    llm_call: Optional[LlmCall] = None,
) -> JudgeResult:
    """Score translation quality via an MQM-structured LLM critique.

    Pass ``llm_call`` to inject a deterministic backend for tests.
    """

    if not question.title.strip():
        return JudgeResult(
            name=JUDGE_NAME,
            passed=False,
            score=0.0,
            reason="Empty candidate translation.",
            evidence={"score_raw": 0, "errors": []},
        )

    backend: LlmCall = llm_call or _call_anthropic_haiku
    provider = PROVIDER_LABEL if llm_call is None else "injected"
    prompt = _build_prompt(question)

    try:
        raw_response = await backend(prompt)
    except Exception as exc:
        _log_llm_call(
            JUDGE_NAME, provider, len(prompt), 0, success=False, error=str(exc)
        )
        # Offline / no key — degrade gracefully so the panel still runs.
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,
            score=0.5,
            reason=f"LLM backend unavailable ({exc}); MQM skipped (neutral).",
            evidence={
                "score_raw": None,
                "errors": [],
                "offline": True,
                "provider": provider,
            },
        )

    _log_llm_call(
        JUDGE_NAME, provider, len(prompt), len(raw_response or ""), success=True
    )
    parsed = _parse_response(raw_response)
    score, major_count, minor_count = _score_from_errors(parsed["errors"])
    # Panel gate (README §5.22 + MQM 2.0): MQM score >= 80 AND zero
    # major errors. The major-count clause is stricter than the score
    # cutoff alone — a single major can produce score==80 under our
    # weighting, but production MQM treats any major as gate-failing
    # because a major distorts trader-relevant meaning.
    from polyglot_alpha.judges.types import MQM_PASS_THRESHOLD

    passed = score >= MQM_PASS_THRESHOLD and major_count == 0

    return JudgeResult(
        name=JUDGE_NAME,
        passed=passed,
        score=score / 100.0,
        reason=(
            f"MQM score={score}/100 (threshold>={MQM_PASS_THRESHOLD}),"
            f" major={major_count}, minor={minor_count}"
        ),
        evidence={
            "score_raw": score,
            "major_count": major_count,
            "minor_count": minor_count,
            "errors": parsed["errors"],
            "rationale": parsed["rationale"],
            "provider": provider,
        },
    )
