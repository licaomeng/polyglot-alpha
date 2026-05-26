"""Layer 5 — Refine.

Single-shot revision of the moderator's winning translator candidate, using
the consolidated critic feedback (``critique_signal``) produced by the
moderator. The same LLM that authored the winning candidate is asked to
edit its own work, preserving the immutable identity fields
(``title``/``category``/``end_date_iso``) and improving the resolution
fields (``resolution_criteria``/``resolution_source``) and question
wording precision.

Contract is deliberately conservative:

* If the LLM returns malformed JSON, refine becomes a no-op and the original
  candidate is returned unchanged with a diff_summary explaining the skip.
* If the LLM call times out (45s default), refine becomes a no-op for the
  same reason. We never block the pipeline on a slow refine.
* Required identity fields that the LLM tries to mutate are silently
  reverted to the originals, so the moderator's contract with downstream
  layers is preserved no matter what the LLM emits.

Public surface:

* :class:`RefineResult` — dataclass returned to the caller.
* :func:`refine_with_critique` — async entrypoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..llm import LLMCallable, make_llm
from ..models import MODEL_REFINE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fields that identify the market and must not be mutated by refine.
PRESERVED_FIELDS: tuple[str, ...] = ("title", "category", "end_date_iso")

# Fields that refine is allowed (and encouraged) to improve.
EDITABLE_FIELDS: tuple[str, ...] = (
    "question_en",
    "resolution_criteria",
    "resolution_source",
    "tags",
)

# Hard cap on the refine LLM call; pipeline must never block longer than this.
DEFAULT_REFINE_TIMEOUT_S: float = 45.0

# Fallback model when neither the candidate nor the caller specifies one.
# Configured via :data:`polyglot_alpha.models.MODEL_REFINE` (env var
# ``MODEL_REFINE``, default Haiku 4.5).
_FALLBACK_MODEL_ID: str = MODEL_REFINE

# Heuristic markers that indicate the resolution_criteria gained precision.
_PRECISION_MARKERS: tuple[str, ...] = (
    "official report by",
    "official report from",
    "official statement by",
    "official statement from",
    "official announcement by",
    "official announcement from",
    "as reported by",
    "as published by",
    "as confirmed by",
    "according to",
    "as defined by",
    "press release from",
    "press release by",
    "before ",
    "by ",
)


_PROMPT_TMPL = (
    "You wrote the following Polymarket question candidate. A panel of "
    "critics reviewed it and surfaced this feedback:\n\n"
    "CRITIQUE: {critique}\n\n"
    "Revise the candidate to address the feedback. Rules:\n"
    "* Preserve the title, category, and end_date_iso fields exactly as "
    "given.\n"
    "* Improve resolution_criteria, resolution_source, and the question "
    "wording so the market is precisely resolvable (name the official "
    "source, the threshold, and the cutoff explicitly).\n"
    "* Keep the binary YES/NO structure.\n"
    "* Return ONLY a single JSON object with the SAME keys as the input "
    "candidate. No prose, no markdown fences.\n\n"
    "EVENT CONTEXT:\n"
    "title_zh: {event_title}\n"
    "body_zh: {event_body}\n\n"
    "INPUT CANDIDATE (JSON):\n{candidate_json}\n"
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class RefineResult:
    """Outcome of one refine pass.

    ``refined_question`` always has the same shape as the input
    ``winning_candidate``. On any failure (timeout, malformed JSON) the
    input dict is returned unchanged so callers can blindly forward
    ``refined_question`` downstream.
    """

    refined_question: Dict[str, Any]
    refine_model: str
    diff_summary: List[str] = field(default_factory=list)
    duration_ms: int = 0
    raw_response: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def refine_with_critique(
    winning_candidate: Dict[str, Any],
    critique_signal: str,
    event: Dict[str, Any],
    *,
    model_id: Optional[str] = None,
    llm: Optional[LLMCallable] = None,
    timeout_s: float = DEFAULT_REFINE_TIMEOUT_S,
) -> RefineResult:
    """One-shot refinement of the winning candidate.

    Parameters
    ----------
    winning_candidate:
        The candidate dict picked by the moderator (Z1). Must contain at
        minimum the immutable identity fields (title, category,
        end_date_iso). Other keys may include ``question_en``,
        ``resolution_criteria``, ``resolution_source``, ``tags``, and a
        ``meta`` block with the translator's model id.
    critique_signal:
        The 1-2 sentence consolidated critique produced by the moderator.
    event:
        The original news event dict (used for prompt context only).
    model_id:
        Override for the LLM that authored the winning candidate. When
        ``None`` the model is read from
        ``winning_candidate['meta']['model']``, falling back through other
        well-known keys, then to a safe default. See
        :func:`_resolve_model_id`.
    llm:
        Test injection point. When ``None``, :func:`make_llm` is used.
    timeout_s:
        Hard timeout on the LLM call. Defaults to 45s.

    Returns
    -------
    RefineResult
        Always returns a result; on failure ``refined_question`` is the
        original candidate (no-op) and ``diff_summary`` explains why.
    """

    started = time.monotonic()
    resolved_model = _resolve_model_id(winning_candidate, model_id)
    call_llm: LLMCallable = llm if llm is not None else make_llm(resolved_model)

    prompt = _build_prompt(winning_candidate, critique_signal, event)

    try:
        raw = await asyncio.wait_for(call_llm(prompt), timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning(
            "refine timed out after %.1fs (model=%s); returning original candidate",
            timeout_s,
            resolved_model,
        )
        return RefineResult(
            refined_question=dict(winning_candidate),
            refine_model=resolved_model,
            diff_summary=[
                f"refine timed out after {timeout_s:.0f}s; original candidate kept"
            ],
            duration_ms=int((time.monotonic() - started) * 1000),
            raw_response="",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("refine LLM call failed: %s", exc)
        return RefineResult(
            refined_question=dict(winning_candidate),
            refine_model=resolved_model,
            diff_summary=[f"refine LLM call raised {type(exc).__name__}; original candidate kept"],
            duration_ms=int((time.monotonic() - started) * 1000),
            raw_response="",
        )

    parsed = _extract_json(raw)
    if not parsed:
        logger.info("refine LLM returned malformed JSON (model=%s)", resolved_model)
        return RefineResult(
            refined_question=dict(winning_candidate),
            refine_model=resolved_model,
            diff_summary=["LLM returned malformed JSON; original candidate kept"],
            duration_ms=int((time.monotonic() - started) * 1000),
            raw_response=raw,
        )

    merged = _merge_refined(winning_candidate, parsed)
    diff = _compute_diff_summary(winning_candidate, merged)
    if not diff:
        diff = ["no observable changes between input and refined candidate"]

    return RefineResult(
        refined_question=merged,
        refine_model=resolved_model,
        diff_summary=diff,
        duration_ms=int((time.monotonic() - started) * 1000),
        raw_response=raw,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_model_id(
    candidate: Dict[str, Any], explicit: Optional[str]
) -> str:
    """Pick the model that should drive the refine call.

    Priority:

    1. ``explicit`` argument from the caller.
    2. ``candidate['meta']['model']`` — the format ``translators.py``
       tags onto each candidate when ``model_id`` is supplied.
    3. Legacy keys: ``candidate['model']``, ``candidate['llm_model']``.
    4. :data:`_FALLBACK_MODEL_ID` (OpenRouter default).
    """

    if explicit:
        return explicit
    meta = candidate.get("meta")
    if isinstance(meta, dict):
        model = meta.get("model")
        if isinstance(model, str) and model:
            return model
    for key in ("model", "llm_model"):
        val = candidate.get(key)
        if isinstance(val, str) and val:
            return val
    return _FALLBACK_MODEL_ID


def _build_prompt(
    candidate: Dict[str, Any], critique_signal: str, event: Dict[str, Any]
) -> str:
    candidate_json = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    return _PROMPT_TMPL.format(
        critique=critique_signal.strip() or "(no critique provided)",
        event_title=str(event.get("title_zh") or event.get("title") or ""),
        event_body=str(event.get("body_zh") or event.get("body") or ""),
        candidate_json=candidate_json,
    )


def _extract_json(text: str) -> Dict[str, Any]:
    """Best-effort JSON parser tolerant of markdown fences and prose."""

    if not text:
        return {}
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    try:
        loaded = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            return {}
        try:
            loaded = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _merge_refined(
    original: Dict[str, Any], refined: Dict[str, Any]
) -> Dict[str, Any]:
    """Build the refined candidate dict.

    Start from a copy of the original (so unknown keys survive), then
    overlay the LLM's edits, then forcibly restore the preserved identity
    fields. This guarantees the moderator's downstream contract on
    title/category/end_date_iso always holds, even if the LLM ignores the
    prompt instructions.
    """

    merged: Dict[str, Any] = dict(original)
    for key, value in refined.items():
        if key in PRESERVED_FIELDS:
            continue  # restored below
        merged[key] = value
    for key in PRESERVED_FIELDS:
        if key in original:
            merged[key] = original[key]
    return merged


def _compute_diff_summary(
    original: Dict[str, Any], refined: Dict[str, Any]
) -> List[str]:
    """Produce 1-3 bullets describing what the refine pass changed."""

    bullets: List[str] = []

    # Title shortening (informational — preserved fields shouldn't change,
    # but we still surface drift attempts if they occurred upstream).
    orig_title = str(original.get("title") or original.get("question_en") or "")
    new_title = str(refined.get("title") or refined.get("question_en") or "")
    if orig_title and new_title and orig_title != new_title:
        delta = len(orig_title) - len(new_title)
        if delta > 0:
            bullets.append(
                f"question wording shortened by {delta} chars"
            )
        elif delta < 0:
            bullets.append(
                f"question wording expanded by {-delta} chars"
            )
        else:
            bullets.append("question wording rephrased (same length)")

    # Resolution criteria edits.
    orig_rc = str(original.get("resolution_criteria") or "").strip()
    new_rc = str(refined.get("resolution_criteria") or "").strip()
    if orig_rc != new_rc:
        added_markers = _new_precision_markers(orig_rc, new_rc)
        if added_markers:
            joined = ", ".join(sorted(added_markers))
            bullets.append(
                f"resolution_criteria gained precision markers: {joined}"
            )
        else:
            len_delta = len(new_rc) - len(orig_rc)
            if len_delta > 0:
                bullets.append(
                    f"resolution_criteria expanded by {len_delta} chars"
                )
            elif len_delta < 0:
                bullets.append(
                    f"resolution_criteria tightened by {-len_delta} chars"
                )
            else:
                bullets.append("resolution_criteria rephrased (same length)")

    # Resolution source edits (added / changed).
    orig_src = str(original.get("resolution_source") or "").strip()
    new_src = str(refined.get("resolution_source") or "").strip()
    if orig_src != new_src:
        if not orig_src and new_src:
            bullets.append(f"resolution_source added: {new_src!r}")
        elif orig_src and not new_src:
            bullets.append("resolution_source removed")
        else:
            bullets.append(
                f"resolution_source changed from {orig_src!r} to {new_src!r}"
            )

    # Cap at 3 bullets per spec.
    return bullets[:3]


def _new_precision_markers(orig: str, new: str) -> set[str]:
    """Return precision markers present in ``new`` but absent in ``orig``."""

    orig_lc = orig.lower()
    new_lc = new.lower()
    added: set[str] = set()
    for marker in _PRECISION_MARKERS:
        if marker in new_lc and marker not in orig_lc:
            # Normalise the trailing space so the diff_summary reads cleanly.
            added.add(marker.strip())
    return added


__all__ = [
    "DEFAULT_REFINE_TIMEOUT_S",
    "EDITABLE_FIELDS",
    "PRESERVED_FIELDS",
    "RefineResult",
    "refine_with_critique",
]
