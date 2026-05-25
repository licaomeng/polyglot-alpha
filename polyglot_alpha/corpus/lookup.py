"""Nearest-neighbour lookup over the Polymarket FAISS index.

Public surface:

    Lookup.load(index_path, meta_path, model_name=...) -> Lookup
    Lookup.find_similar(query, k=5)  -> list[SimilarHit]

The module-level ``find_similar`` helper memoizes a default Lookup keyed
on the canonical corpus/ paths so callers (Judge D8, translator pipeline)
don't have to thread a singleton through their call graphs.

We deliberately split ``Lookup`` from the module-level cache so tests can
hand-construct a Lookup over a small fixture without touching the disk
defaults.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

LOGGER = logging.getLogger(__name__)

DEFAULT_PARQUET_PATH = Path("corpus/polymarket_questions.parquet")
DEFAULT_INDEX_PATH = Path("corpus/polymarket_index.faiss")
DEFAULT_META_PATH = Path("corpus/index_meta.json")
DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass(frozen=True)
class SimilarHit:
    """A single nearest-neighbour result."""

    question: str
    score: float
    market_id: str
    category: str = ""


class Lookup:
    """Holds a FAISS index plus parallel metadata + an encoder model."""

    def __init__(
        self,
        index,
        meta_records: list[dict],
        encoder,
    ) -> None:
        self._index = index
        self._meta = meta_records
        self._encoder = encoder

    # ------------------------------------------------------------------ #
    # Construction.                                                      #
    # ------------------------------------------------------------------ #

    @classmethod
    def load(
        cls,
        *,
        index_path: Path = DEFAULT_INDEX_PATH,
        meta_path: Path = DEFAULT_META_PATH,
        model_name: str = DEFAULT_MODEL_NAME,
        encoder: Optional[object] = None,
    ) -> "Lookup":
        import faiss  # type: ignore

        if not index_path.exists():
            raise FileNotFoundError(f"FAISS index not found: {index_path}")
        if not meta_path.exists():
            raise FileNotFoundError(f"Index metadata not found: {meta_path}")

        index = faiss.read_index(str(index_path))
        meta = json.loads(meta_path.read_text())
        records = meta.get("records", [])
        if not records:
            raise ValueError(f"Empty metadata sidecar: {meta_path}")
        if encoder is None:
            from sentence_transformers import SentenceTransformer  # type: ignore

            encoder = SentenceTransformer(model_name)
        return cls(index, records, encoder)

    @classmethod
    def from_components(
        cls,
        index,
        meta_records: Iterable[dict],
        encoder,
    ) -> "Lookup":
        """Test-friendly constructor that skips disk I/O."""

        return cls(index, list(meta_records), encoder)

    # ------------------------------------------------------------------ #
    # Search.                                                            #
    # ------------------------------------------------------------------ #

    def _encode_query(self, query: str) -> np.ndarray:
        vec = self._encoder.encode(
            [query],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        arr = np.asarray(vec, dtype="float32")
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        return arr

    def find_similar(self, query: str, k: int = 5) -> list[SimilarHit]:
        if not query or not query.strip():
            return []
        k = max(1, min(int(k), self._index.ntotal))
        vec = self._encode_query(query)
        scores, indices = self._index.search(vec, k)
        hits: list[SimilarHit] = []
        for score, idx in zip(scores[0].tolist(), indices[0].tolist()):
            if idx < 0 or idx >= len(self._meta):
                continue
            rec = self._meta[idx]
            hits.append(
                SimilarHit(
                    question=str(rec.get("question", "")),
                    score=float(score),
                    market_id=str(rec.get("market_id", "")),
                    category=str(rec.get("category", "")),
                )
            )
        return hits


# --------------------------------------------------------------------------- #
# Module-level cached singleton.                                              #
# --------------------------------------------------------------------------- #

_DEFAULT_LOOKUP_LOCK = threading.Lock()
_DEFAULT_LOOKUP: Optional[Lookup] = None


def _get_default_lookup() -> Lookup:
    global _DEFAULT_LOOKUP
    if _DEFAULT_LOOKUP is not None:
        return _DEFAULT_LOOKUP
    with _DEFAULT_LOOKUP_LOCK:
        if _DEFAULT_LOOKUP is None:
            _DEFAULT_LOOKUP = Lookup.load()
    return _DEFAULT_LOOKUP


def find_similar(query: str, k: int = 5) -> list[SimilarHit]:
    """Top-level convenience used by Judge D8 and the translator pipeline."""

    return _get_default_lookup().find_similar(query, k=k)


def _reset_default_lookup_for_tests() -> None:
    """Drop the cached singleton; called from tests."""

    global _DEFAULT_LOOKUP
    with _DEFAULT_LOOKUP_LOCK:
        _DEFAULT_LOOKUP = None
