"""D8 - Duplicate detection vs the Polymarket corpus.

Embeds the candidate title with ``sentence-transformers/all-MiniLM-L6-v2``
and queries the FAISS index shipped by T5 at ``corpus/polymarket_index.faiss``
(75,897 live Polymarket markets, IP-normalized so inner product == cosine).
Metadata for the neighbor records is in ``corpus/index_meta.json``.

If the embedding model or FAISS index is unavailable, the judge does
**not** silently report PASS. Instead it returns ``passed=True`` (so the
hard gate at the panel aggregator doesn't blanket-reject every event
when D8 is down) but stamps ``panel_budget_exceeded=True`` and
``soft_skip=True`` on its ``evidence`` so the panel/UI surface an
INSUFFICIENT_DATA partial banner — matching the W9-A / FIX-C
soft-skip-with-visibility contract. The W8 audit explicitly flagged
silent-PASS-on-model-failure as fake-success; this judge is now loud.

This is a *hard* gate: a confirmed duplicate (cosine >= 0.92 per
README §5.22) fails the panel.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from polyglot_alpha.judges.types import (
    DUPLICATE_COSINE_THRESHOLD,
    JudgeResult,
    PanelQuestion,
)

logger = logging.getLogger(__name__)

JUDGE_NAME = "d8_duplicate_detection"
DEFAULT_INDEX_PATH = Path("corpus/polymarket_index.faiss")
DEFAULT_METADATA_PATH = Path("corpus/index_meta.json")
DEFAULT_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

# Sentinel reason codes surfaced via JudgeResult.reason / evidence so the
# operator can tell "D8 model crashed" apart from "D8 saw no duplicate".
REASON_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
REASON_INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"

_model_cache: dict[str, Any] = {}
_model_load_error: dict[str, str] = {}
_index_cache: dict[str, Any] = {}
_metadata_cache: dict[str, list[dict[str, Any]]] = {}


def _load_metadata(metadata_path: Path) -> Optional[list[dict[str, Any]]]:
    """Load ``index_meta.json`` records keyed by FAISS row index.

    Returns ``None`` on any failure (file missing, malformed). The caller
    can still produce a verdict — we just won't have a human-readable
    neighbor question in the evidence.
    """

    key = str(metadata_path.resolve())
    if key in _metadata_cache:
        return _metadata_cache[key]
    try:
        if not metadata_path.exists():
            return None
        with metadata_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list):
            return None
        _metadata_cache[key] = records
        return records
    except Exception:
        return None


def _load_embedding_model() -> Optional[Any]:
    """Load (and memo-cache) the SBert encoder used for D8.

    Returns the loaded ``SentenceTransformer`` or ``None`` if the model
    cannot be loaded (network down, HF unreachable, tokenizer missing).
    The last failure reason is stashed in ``_model_load_error`` so the
    health check and the judge call site can surface it instead of
    silently passing.
    """

    if DEFAULT_MODEL_ID in _model_cache:
        return _model_cache[DEFAULT_MODEL_ID]
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(DEFAULT_MODEL_ID)
        _model_cache[DEFAULT_MODEL_ID] = model
        _model_load_error.pop(DEFAULT_MODEL_ID, None)
        return model
    except Exception as exc:  # noqa: BLE001 - we want broad coverage
        _model_cache[DEFAULT_MODEL_ID] = None
        _model_load_error[DEFAULT_MODEL_ID] = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "d8.model_load: FAILED model=%s reason=%s",
            DEFAULT_MODEL_ID,
            _model_load_error[DEFAULT_MODEL_ID],
        )
        return None


def get_last_model_load_error() -> Optional[str]:
    """Return the most recent SBert load failure (or ``None`` if healthy).

    Exposed for ``scripts/check_d8_health.py`` and the startup pre-warm
    logger so they can report *why* the model wasn't loaded.
    """

    return _model_load_error.get(DEFAULT_MODEL_ID)


def _load_index(index_path: Path) -> Optional[Any]:
    key = str(index_path.resolve())
    if key in _index_cache:
        return _index_cache[key]
    try:
        import faiss  # type: ignore

        if not index_path.exists():
            _index_cache[key] = None
            return None
        idx = faiss.read_index(str(index_path))
        _index_cache[key] = idx
        return idx
    except Exception:
        _index_cache[key] = None
        return None


def _cosine_from_inner_product(value: float) -> float:
    """If the FAISS index is normalized, IP == cosine; otherwise clamp."""

    return max(-1.0, min(1.0, float(value)))


async def judge_d8_duplicate_detection(
    question: PanelQuestion,
    index_path: Path | str | None = None,
    threshold: float = DUPLICATE_COSINE_THRESHOLD,
    embed_override: Optional[Any] = None,
    index_override: Optional[Any] = None,
    metadata_path: Path | str | None = None,
) -> JudgeResult:
    """Search the corpus for a near-duplicate of ``question.title``.

    ``embed_override`` / ``index_override`` are escape hatches for tests
    (pass a stub model and FAISS index directly).
    """

    title = question.title.strip()
    if not title:
        return JudgeResult(
            name=JUDGE_NAME,
            passed=False,
            score=0.0,
            reason="Empty title.",
            evidence={"max_similarity": None},
        )

    path = Path(index_path) if index_path else DEFAULT_INDEX_PATH
    # First-time SentenceTransformer / FAISS loads can take 60+ seconds
    # which would otherwise block the asyncio event loop and stall the
    # whole 11-judge panel. Push the load into a worker thread.
    if embed_override is None:
        model = await asyncio.to_thread(_load_embedding_model)
    else:
        model = embed_override
    if index_override is None:
        index = await asyncio.to_thread(_load_index, path)
    else:
        index = index_override

    if model is None or index is None:
        # W13-D: do NOT silently report PASS. The verdict is still
        # ``passed=True`` (otherwise every event would hard-fail on the
        # D8 hard gate when SBert / FAISS are unreachable), but the
        # ``soft_skip`` / ``panel_budget_exceeded`` evidence flags make
        # the unavailability visible to the panel aggregator and to the
        # UI's INSUFFICIENT_DATA partial banner. See W8 audit + FIX-C.
        if model is None:
            reason_code = REASON_MODEL_UNAVAILABLE
            human_reason = (
                f"{REASON_MODEL_UNAVAILABLE}: embedding model "
                f"'{DEFAULT_MODEL_ID}' unavailable"
            )
            load_err = _model_load_error.get(DEFAULT_MODEL_ID)
            if load_err:
                human_reason += f" ({load_err})"
        else:
            reason_code = REASON_INDEX_UNAVAILABLE
            human_reason = (
                f"{REASON_INDEX_UNAVAILABLE}: FAISS index not loaded "
                f"({path})"
            )
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,
            score=1.0,
            reason=human_reason,
            evidence={
                "max_similarity": None,
                "index_path": str(path),
                "index_exists": path.exists() if index_override is None else True,
                "model_loaded": model is not None,
                # These two flags are read by judges.panel._aggregate
                # (line ~682) to populate the per-judge dossier with
                # panelBudgetExceeded + softSkip — the same shape the
                # W2-1 UI uses to render "INSUFFICIENT_DATA · partial".
                "panel_budget_exceeded": True,
                "soft_skip": True,
                "partial": True,
                "insufficient_data": True,
                "reason_code": reason_code,
                "model_load_error": _model_load_error.get(DEFAULT_MODEL_ID),
            },
        )

    def _encode_and_search() -> tuple[Any, Any, Optional[str]]:
        emb = model.encode(
            [title], normalize_embeddings=True, show_progress_bar=False
        )
        try:
            import numpy as np

            emb = np.asarray(emb, dtype="float32")
        except Exception:
            pass
        try:
            d, i = index.search(emb, 1)
            return d, i, None
        except Exception as exc:  # pragma: no cover - index shape mismatch
            return None, None, str(exc)

    distances, indices, search_err = await asyncio.to_thread(_encode_and_search)
    if search_err is not None:
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,
            score=1.0,
            reason=f"FAISS search failed ({search_err}); skipping duplicate check.",
            evidence={"max_similarity": None, "error": search_err},
        )

    similarity = _cosine_from_inner_product(distances[0][0])
    top_idx = int(indices[0][0])
    # README §5.22: cosine >= 0.92 -> duplicate. Use >= so the threshold
    # boundary itself is treated as a duplicate.
    is_duplicate = similarity >= threshold

    neighbor_text: Optional[str] = None
    neighbor_record: Optional[dict[str, Any]] = None
    meta_path = Path(metadata_path) if metadata_path else DEFAULT_METADATA_PATH
    records = _load_metadata(meta_path)
    if records is not None and 0 <= top_idx < len(records):
        neighbor_record = records[top_idx]
        neighbor_text = str(neighbor_record.get("question", "")) or None

    return JudgeResult(
        name=JUDGE_NAME,
        passed=not is_duplicate,
        score=1.0 - similarity if is_duplicate else 1.0,
        reason=(
            f"Near-duplicate found (cosine={similarity:.3f} >= {threshold})"
            + (f": '{neighbor_text}'" if neighbor_text else "")
            if is_duplicate
            else f"No duplicate (top cosine={similarity:.3f})"
        ),
        evidence={
            "max_similarity": similarity,
            "threshold": threshold,
            "neighbor_index": top_idx,
            "neighbor_question": neighbor_text,
            "neighbor_record": neighbor_record,
            "model_id": DEFAULT_MODEL_ID,
        },
    )
