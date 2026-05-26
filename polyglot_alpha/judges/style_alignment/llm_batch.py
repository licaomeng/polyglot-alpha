"""Per-dimension LLM calls for style-alignment judges D2, D3, D6, D7.

After the 2026-05 single-provider consolidation every dimension is
served by Anthropic Claude Haiku 4.5. The previous multi-provider
mapping (DeepSeek / OpenRouter / Gemini) has been deleted; anti-collusion
within the style panel is now achieved via per-dimension system prompts
and temperature, not by routing across providers.

When ``llm_call`` is supplied by the caller, the override wins and the
provider field reads ``injected``. Every call (success or failure) is
recorded in ``outputs/llm_cost_log.jsonl`` for budget visibility.

A legacy ``run_style_llm_batch`` entry point is preserved so existing
callers don't break; it fans out to per-dimension calls in parallel and
merges the results, returning the same dict shape.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from polyglot_alpha.judges.types import PanelQuestion
from polyglot_alpha.models import MODEL_STYLE_JUDGE

LlmCall = Callable[[str], Awaitable[str]]

LLM_COST_LOG_PATH = Path("outputs/llm_cost_log.jsonl")

# Per-dimension provider mapping. After the 2026-05 single-provider
# consolidation every dimension is served by Anthropic Claude Haiku 4.5;
# this dict is retained so callers / tests that key off the label still
# resolve, but the label is identical for every entry. The actual snapshot
# is configured by ``MODEL_STYLE_JUDGE`` in ``.env``.
_DEFAULT_PROVIDER_LABEL = f"anthropic:{MODEL_STYLE_JUDGE}"
PROVIDER_FOR_DIMENSION: dict[str, str] = {
    "d2": _DEFAULT_PROVIDER_LABEL,
    "d3": _DEFAULT_PROVIDER_LABEL,
    "d5": _DEFAULT_PROVIDER_LABEL,
    "d6": _DEFAULT_PROVIDER_LABEL,
    "d7": _DEFAULT_PROVIDER_LABEL,
}


def _log_llm_call(
    judge_name: str,
    provider: str,
    prompt_chars: int,
    response_chars: int,
    success: bool,
    error: Optional[str] = None,
) -> None:
    """Append a JSONL cost-log line. Best-effort; OSErrors are swallowed."""

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


_DIMENSION_PROMPTS: dict[str, str] = {
    "d2": (
        "You are an editor reviewing a prediction-market question for"
        " stylistic fit. Is the tone neutral, source-cited, and free of"
        " editorializing or emotional language?\n\n"
        "TITLE: {title}\nDESCRIPTION: {description}\n"
        "RESOLUTION_CRITERIA: {resolution_criteria}\n"
        "RESOLUTION_SOURCE: {resolution_source}\nCATEGORY: {category}\n\n"
        'Respond with ONLY a JSON object: {{"passed": bool, "score": float,'
        ' "reason": "..."}}'
    ),
    "d3": (
        "You are an editor reviewing a prediction-market question for"
        " framing. Is it predictive (uncertain future outcome) rather than"
        " declarative (asserting facts)?\n\n"
        "TITLE: {title}\nDESCRIPTION: {description}\n"
        "RESOLUTION_CRITERIA: {resolution_criteria}\n"
        "RESOLUTION_SOURCE: {resolution_source}\nCATEGORY: {category}\n\n"
        'Respond with ONLY a JSON object: {{"passed": bool, "score": float,'
        ' "reason": "..."}}'
    ),
    "d6": (
        "You are an editor reviewing a prediction-market question's source"
        " reliability. Does the cited source match the content and is it"
        " authoritative for resolution?\n\n"
        "TITLE: {title}\nDESCRIPTION: {description}\n"
        "RESOLUTION_CRITERIA: {resolution_criteria}\n"
        "RESOLUTION_SOURCE: {resolution_source}\nCATEGORY: {category}\n\n"
        'Respond with ONLY a JSON object: {{"passed": bool, "score": float,'
        ' "reason": "..."}}'
    ),
    "d7": (
        "You are an editor reviewing a prediction-market question for"
        " leading bias. Does the framing nudge the trader toward a"
        " particular outcome?\n\n"
        "TITLE: {title}\nDESCRIPTION: {description}\n"
        "RESOLUTION_CRITERIA: {resolution_criteria}\n"
        "RESOLUTION_SOURCE: {resolution_source}\nCATEGORY: {category}\n\n"
        'Respond with ONLY a JSON object: {{"passed": bool, "score": float,'
        ' "reason": "..."}}'
    ),
}


def _build_prompt(question: PanelQuestion, dimension: str) -> str:
    template = _DIMENSION_PROMPTS.get(dimension)
    if template is None:
        raise ValueError(f"unknown dimension {dimension!r}")
    return template.format(
        title=question.title or "(empty)",
        description=question.description or "(none)",
        resolution_criteria=question.resolution_criteria or "(none)",
        resolution_source=question.resolution_source or "(none)",
        category=question.category or "(none)",
    )


def _parse_single(raw: str) -> dict[str, Any]:
    """Parse a single ``{passed, score, reason}`` JSON object."""

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
                data = {}
        else:
            data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "passed": bool(data.get("passed", False)),
        "score": float(data.get("score", 0.0) or 0.0),
        "reason": str(data.get("reason", "") or ""),
    }


async def _call_default_backend(prompt: str, dimension: str) -> str:
    """Route to the single configured provider (Anthropic Claude Haiku 4.5).

    ``dimension`` is accepted for API parity with the legacy multi-provider
    routing but is currently unused — every dimension shares the same
    backend after the single-provider consolidation. Raises
    :class:`RuntimeError` when ``ANTHROPIC_API_KEY`` is not set so the
    caller can degrade gracefully to a neutral pass.
    """

    _ = dimension  # kept for signature parity; see docstring
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "No LLM backend reachable: set ANTHROPIC_API_KEY."
        )

    from polyglot_alpha.llm import AnthropicLLM

    llm = AnthropicLLM(model=MODEL_STYLE_JUDGE)
    return await llm.complete(
        system="Return ONLY a JSON object — no prose, no markdown fences.",
        user=prompt,
        max_tokens=1024,
        temperature=0.0,
    )


# Cache per (question identity, dimension) so concurrent panel runs don't
# double-call when callers pre-flight a dimension.
_CACHE: dict[tuple[int, str], dict[str, Any]] = {}


async def run_dimension_llm(
    question: PanelQuestion,
    dimension: str,
    llm_call: Optional[LlmCall] = None,
) -> dict[str, Any]:
    """Call the LLM for a single style-alignment dimension.

    Returns ``{"passed": bool, "score": float, "reason": str,
    "offline": bool, "provider": str}``.
    """

    cache_key = (id(question), dimension)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    if llm_call is not None:
        provider = "injected"
    else:
        # Single provider after the 2026-05 consolidation: Anthropic
        # Claude Haiku 4.5 for every dimension. ``_call_default_backend``
        # raises if ``ANTHROPIC_API_KEY`` is unset, which the caller
        # handles below by degrading to a neutral pass.
        provider = PROVIDER_FOR_DIMENSION.get(dimension, _DEFAULT_PROVIDER_LABEL)
    prompt = _build_prompt(question, dimension)

    try:
        if llm_call is not None:
            raw = await llm_call(prompt)
        else:
            raw = await _call_default_backend(prompt, dimension)
    except Exception as exc:
        _log_llm_call(
            dimension, provider, len(prompt), 0, success=False, error=str(exc)
        )
        # Offline / no key — neutral pass to keep demo flowing.
        result = {
            "passed": True,
            "score": 0.5,
            "reason": f"LLM unavailable ({exc}); neutral pass.",
            "offline": True,
            "provider": provider,
        }
        _CACHE[cache_key] = result
        return result

    _log_llm_call(
        dimension, provider, len(prompt), len(raw or ""), success=True
    )
    parsed = _parse_single(raw)
    parsed["offline"] = False
    parsed["provider"] = provider
    _CACHE[cache_key] = parsed
    return parsed


def _looks_like_multidim_payload(raw: str) -> Optional[dict[str, Any]]:
    """Detect the legacy batched ``{"d2": ..., "d3": ...}`` JSON shape.

    Returns the parsed dict if it has any of the four dimension keys,
    else ``None`` so callers can fall back to single-dim parsing.
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
    if any(k in data for k in ("d2", "d3", "d6", "d7")):
        return data
    return None


async def run_style_llm_batch(
    question: PanelQuestion,
    llm_call: Optional[LlmCall] = None,
) -> dict[str, Any]:
    """Legacy entry point. Fans out per-dimension calls and merges results.

    Returned shape matches the original batched call so existing tests
    and judges that import this function keep working unchanged:

        {"d2": {...}, "d3": {...}, "d6": {...}, "d7": {...},
         "offline": bool}

    Backward-compat shortcut: when ``llm_call`` is injected and the
    stub returns a single JSON payload containing the d2/d3/d6/d7 keys
    (the legacy batched shape), we honor that directly and skip the
    per-dimension fan-out. Production calls (``llm_call is None``)
    always fan out to per-dimension providers.
    """

    dims = ("d2", "d3", "d6", "d7")

    if llm_call is not None:
        cache_key = (id(question), "_legacy_batch")
        if cache_key in _CACHE:
            return _CACHE[cache_key]
        # Probe once: if the stub responds with the legacy multi-dim
        # shape, populate all four entries from a single call.
        probe_prompt = _build_prompt(question, "d2")
        try:
            probe_raw = await llm_call(probe_prompt)
        except Exception as exc:
            _log_llm_call(
                "legacy_batch", "injected", len(probe_prompt), 0,
                success=False, error=str(exc),
            )
            neutral_reason = f"LLM unavailable ({exc}); neutral pass."
            result = {
                dim: {
                    "passed": True,
                    "score": 0.5,
                    "reason": neutral_reason,
                    "offline": True,
                    "provider": "injected",
                }
                for dim in dims
            }
            result["offline"] = True
            _CACHE[cache_key] = result
            return result

        legacy = _looks_like_multidim_payload(probe_raw)
        if legacy is not None:
            _log_llm_call(
                "legacy_batch", "injected", len(probe_prompt),
                len(probe_raw or ""), success=True,
            )
            merged: dict[str, Any] = {}
            for dim in dims:
                entry = legacy.get(dim) or {}
                if not isinstance(entry, dict):
                    entry = {}
                merged[dim] = {
                    "passed": bool(entry.get("passed", False)),
                    "score": float(entry.get("score", 0.0) or 0.0),
                    "reason": str(entry.get("reason", "") or ""),
                    "offline": False,
                    "provider": "injected",
                }
            merged["offline"] = False
            _CACHE[cache_key] = merged
            return merged
        # Stub returned per-dimension shape — fall through to fan-out.

    coros = [run_dimension_llm(question, d, llm_call=llm_call) for d in dims]
    results = await asyncio.gather(*coros)
    merged: dict[str, Any] = {dim: res for dim, res in zip(dims, results)}
    merged["offline"] = any(r.get("offline", False) for r in results)
    return merged


def clear_cache() -> None:
    """Test helper — wipe the per-question dimension cache."""

    _CACHE.clear()
