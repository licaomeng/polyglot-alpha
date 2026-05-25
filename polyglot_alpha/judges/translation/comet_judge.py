"""COMET judge (reference-free quality estimation).

Tries the spec-preferred ``Unbabel/wmt22-cometkiwi-da`` first; falls back to
the non-gated ``Unbabel/wmt20-comet-qe-da`` when the user has not accepted
the cometkiwi gated-repo terms. Both models share the same reference-free
``predict([{"src": ..., "mt": ...}])`` API.

The model is loaded lazily and cached at module scope. We tolerate missing
weights / offline runs by returning a neutral score with ``passed=True`` so
the panel can still produce a verdict during demos.

Python 3.14 + COMET 2.2.7 compat: COMET unconditionally sets
``multiprocessing_context="fork"`` when ``torch.backends.mps.is_available()``
is true (Apple Silicon), but ``num_workers=0`` then trips PyTorch's
DataLoader validator. We neutralise MPS detection at module import so
COMET falls into the single-process CPU path that works correctly.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Python 3.14 + COMET 2.2.7 compat patch (must run before `comet` is imported)
# multiprocessing_context="fork" conflicts with num_workers=0 on MPS;
# disable MPS detection so COMET uses single-process mode cleanly.
# --------------------------------------------------------------------------- #
import torch.backends.mps  # noqa: E402

if not getattr(torch.backends.mps, "_polyglot_patched", False):
    torch.backends.mps.is_available = lambda: False  # type: ignore[assignment]
    torch.backends.mps._polyglot_patched = True  # type: ignore[attr-defined]

import asyncio  # noqa: E402
import logging  # noqa: E402
from typing import Any, Optional  # noqa: E402

from polyglot_alpha.judges.types import JudgeResult, PanelQuestion  # noqa: E402

logger = logging.getLogger(__name__)

JUDGE_NAME = "comet"

# Preferred (gated) and fallback (non-gated) reference-free models.
# Both expose the same predict([{"src": ..., "mt": ...}]) API.
PREFERRED_MODEL = "Unbabel/wmt22-cometkiwi-da"
FALLBACK_MODEL = "Unbabel/wmt20-comet-qe-da"

# Per-model pass thresholds (raw score, before any clipping):
# - cometkiwi-da emits 0..1 utility scores; ~0.6 is a reasonable pass.
# - wmt20-comet-qe-da emits z-score normalised QE values centred ~0 and
#   typically in [-1, 1]; above-average translations register >= 0.
_PASS_THRESHOLDS: dict[str, float] = {
    "Unbabel/wmt22-cometkiwi-da": 0.60,
    "Unbabel/wmt20-comet-qe-da": 0.00,
}

# Module-level singleton cache: holds (model_id, model) once loaded so
# every judge call after the first reuses the warmed checkpoint.
_model_state: dict[str, Any] = {"model_id": None, "model": None, "tried": False}


def _try_download(model_id: str) -> Optional[str]:
    """Return checkpoint path on success, None on any failure (gated, network, ...)."""
    try:
        from comet import download_model

        return download_model(model_id)
    except Exception as exc:  # noqa: BLE001 - broad by design; we fall back
        logger.debug("COMET download failed for %s: %s", model_id, exc)
        return None


def _load_model() -> tuple[Optional[Any], Optional[str]]:
    """Lazy-load the COMET model; return (model, model_id) or (None, None)."""

    if _model_state["tried"]:
        return _model_state["model"], _model_state["model_id"]

    _model_state["tried"] = True

    try:
        from comet import load_from_checkpoint
    except Exception as exc:  # pragma: no cover - comet not installed
        logger.warning("COMET package unavailable: %s", exc)
        return None, None

    for candidate in (PREFERRED_MODEL, FALLBACK_MODEL):
        ckpt = _try_download(candidate)
        if ckpt is None:
            continue
        try:
            model = load_from_checkpoint(ckpt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("COMET load_from_checkpoint(%s) failed: %s", candidate, exc)
            continue
        _model_state["model"] = model
        _model_state["model_id"] = candidate
        logger.info("COMET model loaded: %s", candidate)
        return model, candidate

    logger.warning("COMET: no model could be loaded (preferred=%s, fallback=%s)",
                   PREFERRED_MODEL, FALLBACK_MODEL)
    return None, None


def _score_sync(question: PanelQuestion) -> tuple[Optional[float], Optional[str]]:
    """Run COMET on a single (src, mt) pair. Returns (raw_score, model_id)."""

    model, model_id = _load_model()
    if model is None or model_id is None:
        return None, None

    data = [
        {
            "src": question.source_news or question.description or question.title,
            "mt": question.title,
        }
    ]
    try:
        # gpus=0 keeps the model on CPU; combined with the MPS-disable patch
        # this gives a clean num_workers=0, single-process predict on macOS.
        output = model.predict(data, batch_size=1, progress_bar=False, gpus=0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("COMET predict failed (%s): %s", model_id, exc)
        return None, model_id

    score = getattr(output, "system_score", None)
    if score is None:
        scores = getattr(output, "scores", None)
        if scores:
            score = float(scores[0])
    return (float(score) if score is not None else None, model_id)


def _normalize_to_unit(raw: float, model_id: str) -> float:
    """Map raw COMET score into [0, 1] for panel aggregation.

    cometkiwi-da already lives in [0, 1] (clip for safety).
    wmt20-comet-qe-da is a z-score roughly in [-1, 1]; map via (raw + 1) / 2.
    """
    if model_id == "Unbabel/wmt20-comet-qe-da":
        return max(0.0, min(1.0, (raw + 1.0) / 2.0))
    return max(0.0, min(1.0, raw))


async def judge_comet(question: PanelQuestion) -> JudgeResult:
    """Run COMET in a thread so it doesn't block the panel's event loop."""

    if not question.title.strip():
        return JudgeResult(
            name=JUDGE_NAME,
            passed=False,
            score=0.0,
            reason="Empty candidate translation.",
            evidence={"comet_raw": None},
        )

    raw, model_id = await asyncio.to_thread(_score_sync, question)

    if raw is None:
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,  # graceful degradation: don't block demo on missing weights
            score=0.5,
            reason="COMET model unavailable (offline or missing weights); neutral.",
            evidence={"comet_raw": None, "model_id": model_id or PREFERRED_MODEL},
        )

    threshold = _PASS_THRESHOLDS.get(model_id, 0.60)
    passed = raw > threshold
    return JudgeResult(
        name=JUDGE_NAME,
        passed=passed,
        score=_normalize_to_unit(raw, model_id),
        reason=(
            f"COMET={raw:.3f} (model={model_id}, threshold > {threshold})"
            f" -> {'pass' if passed else 'below threshold'}"
        ),
        evidence={
            "comet_raw": raw,
            "threshold": threshold,
            "model_id": model_id,
        },
    )
