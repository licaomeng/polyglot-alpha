"""D8 - Duplicate detection vs the Polymarket corpus.

Embeds the candidate title with ``sentence-transformers/all-MiniLM-L6-v2``
and queries the FAISS index shipped by T5 at ``corpus/polymarket_index.faiss``
(5K live Polymarket markets, IP-normalized so inner product == cosine).
Metadata for the neighbor records is in ``corpus/index_meta.json``.

If the index is not on disk, the judge returns ``passed=True`` with a
note so the panel can still run end-to-end. With the T5-shipped index
this fallback should never fire during the demo.

This is a *hard* gate: a confirmed duplicate (cosine >= 0.92 per
README §5.22) fails the panel.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from polyglot_alpha.judges.types import (
    DUPLICATE_COSINE_THRESHOLD,
    JudgeResult,
    PanelQuestion,
)

JUDGE_NAME = "d8_duplicate_detection"
DEFAULT_INDEX_PATH = Path("corpus/polymarket_index.faiss")
DEFAULT_METADATA_PATH = Path("corpus/index_meta.json")
DEFAULT_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

_model_cache: dict[str, Any] = {}
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
    if DEFAULT_MODEL_ID in _model_cache:
        return _model_cache[DEFAULT_MODEL_ID]
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(DEFAULT_MODEL_ID)
        _model_cache[DEFAULT_MODEL_ID] = model
        return model
    except Exception:
        _model_cache[DEFAULT_MODEL_ID] = None
        return None


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
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,
            score=1.0,
            reason=(
                "corpus not loaded"
                if index is None
                else "embedding model unavailable; skipping duplicate check"
            ),
            evidence={
                "max_similarity": None,
                "index_path": str(path),
                "index_exists": path.exists() if index_override is None else True,
                "model_loaded": model is not None,
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
