"""Embed question titles with MiniLM and build a FAISS index.

We normalize embeddings to unit length and use ``IndexFlatIP`` so that
inner-product search is equivalent to cosine similarity. The index plus
a JSON metadata sidecar (idx -> market_id, question, category) are
written next to the parquet so the lookup module can reload everything
in one shot.

Two CLI flows:

  * Build-from-parquet (original):
      ``python -m polyglot_alpha.corpus.embed --parquet ... --index ...``
  * Reconcile-all (Fix 1): embed every ``corpus_markets`` row whose
    ``embedding_idx`` is NULL, append to the existing FAISS index, and
    update both the DB column and ``corpus/index_meta.json`` in lockstep:
      ``python -m polyglot_alpha.corpus.embed --reconcile-all
          --batch-size 1000``

The reconcile-all path is idempotent: it skips rows that already have an
``embedding_idx`` and skips rows whose ``question`` is empty/null.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
DEFAULT_BATCH_SIZE = 64
DEFAULT_RECONCILE_BATCH_SIZE = 1000

DEFAULT_INDEX_PATH = Path("corpus/polymarket_index.faiss")
DEFAULT_META_PATH = Path("corpus/index_meta.json")


def load_questions_dataframe(parquet_path: Path) -> pd.DataFrame:
    """Load the parquet corpus and validate required columns."""

    df = pd.read_parquet(parquet_path)
    required = {"market_id", "question", "category"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Parquet {parquet_path} missing required columns: {sorted(missing)}"
        )
    return df


def embed_texts(
    texts: Iterable[str],
    *,
    model_name: str = DEFAULT_MODEL_NAME,
    batch_size: int = DEFAULT_BATCH_SIZE,
    model: Optional[object] = None,
) -> np.ndarray:
    """Encode an iterable of texts to a (N, EMBEDDING_DIM) float32 matrix.

    Pass a pre-loaded ``model`` (anything with an ``encode(...)`` method)
    to bypass importing sentence_transformers — useful in unit tests where
    we hand in a deterministic stub.
    """

    if model is None:
        # Local import: heavy dependency that we don't want at module load
        # time for tests that stub out the model entirely.
        from sentence_transformers import SentenceTransformer  # type: ignore

        model = SentenceTransformer(model_name)

    encoded = model.encode(
        list(texts),
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    arr = np.asarray(encoded, dtype="float32")
    if arr.ndim != 2:
        raise RuntimeError(f"Encoder returned non-2D array: shape={arr.shape}")
    return arr


def build_faiss_index(embeddings: np.ndarray):
    """Return a fresh ``IndexFlatIP`` populated with the given vectors."""

    import faiss  # type: ignore

    if embeddings.dtype != np.float32:
        embeddings = embeddings.astype("float32")
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    return index


def write_index(index, dest: Path) -> Path:
    import faiss  # type: ignore

    dest.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(dest))
    return dest


def write_metadata(df: pd.DataFrame, dest: Path) -> Path:
    """Persist the row order metadata used to map FAISS idx -> market info."""

    dest.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "idx": int(i),
            "market_id": str(row["market_id"]),
            "question": str(row["question"]),
            "category": str(row.get("category") or ""),
        }
        for i, row in df.reset_index(drop=True).iterrows()
    ]
    dest.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2))
    return dest


def build_corpus_index(
    parquet_path: Path,
    *,
    index_path: Path,
    meta_path: Path,
    model_name: str = DEFAULT_MODEL_NAME,
    model: Optional[object] = None,
) -> tuple[Path, Path]:
    """End-to-end: parquet -> embeddings -> FAISS index + meta sidecar."""

    df = load_questions_dataframe(parquet_path)
    LOGGER.info("loaded %d rows from %s", len(df), parquet_path)
    embeddings = embed_texts(
        df["question"].astype(str).tolist(),
        model_name=model_name,
        model=model,
    )
    LOGGER.info(
        "encoded %d embeddings (dim=%d)", embeddings.shape[0], embeddings.shape[1]
    )
    index = build_faiss_index(embeddings)
    write_index(index, index_path)
    write_metadata(df, meta_path)
    LOGGER.info("wrote index -> %s, meta -> %s", index_path, meta_path)
    return index_path, meta_path


# --------------------------------------------------------------------------- #
# Reconcile-all: embed every NULL-idx CorpusMarket and grow the FAISS index.  #
# --------------------------------------------------------------------------- #


def reconcile_all(
    *,
    index_path: Path = DEFAULT_INDEX_PATH,
    meta_path: Path = DEFAULT_META_PATH,
    batch_size: int = DEFAULT_RECONCILE_BATCH_SIZE,
    model_name: str = DEFAULT_MODEL_NAME,
    model: Optional[object] = None,
    dry_run: bool = False,
    max_rows: Optional[int] = None,
) -> dict[str, int]:
    """Embed every ``CorpusMarket`` row whose ``embedding_idx`` is NULL.

    The function APPENDS to the existing FAISS index (so older indices
    remain stable), updates each row's ``embedding_idx`` to its new
    position, and rewrites ``corpus/index_meta.json`` with the merged
    records.

    Args:
        index_path: Existing FAISS index path (``IndexFlatIP``, dim=384).
        meta_path: JSON sidecar to be rewritten with merged records.
        batch_size: Rows per embedding batch — bounds peak memory.
        model_name: HF model identifier for ``SentenceTransformer``.
        model: Optional pre-loaded encoder (test override).
        dry_run: If True, only report what would be embedded; do not
            touch the index, meta file, or DB.
        max_rows: Optional hard cap on the number of rows processed
            (useful for partial test runs).

    Returns:
        Stats dict: ``{"to_embed": N, "embedded": M, "skipped_empty": K,
        "dry_run": bool}``.
    """

    # Local imports — heavyweight & DB-bound; avoid at module load time.
    import faiss  # type: ignore

    from polyglot_alpha.persistence import session_scope
    from polyglot_alpha.persistence.models import CorpusMarket
    from sqlalchemy import select

    if not index_path.exists():
        raise FileNotFoundError(
            f"FAISS index not found: {index_path}. Build it first via "
            "`python -m polyglot_alpha.corpus.embed --parquet ...`."
        )
    if not meta_path.exists():
        raise FileNotFoundError(
            f"Meta sidecar not found: {meta_path}. Build it first."
        )

    # --- Snapshot the rows to embed (read-only). ------------------------- #
    null_rows: list[tuple[str, str, Optional[str]]] = []
    skipped_empty = 0
    with session_scope() as session:
        stmt = select(
            CorpusMarket.market_id, CorpusMarket.question, CorpusMarket.category
        ).where(CorpusMarket.embedding_idx.is_(None))
        for market_id, question, category in session.execute(stmt):
            qtext = (question or "").strip()
            if not qtext:
                skipped_empty += 1
                continue
            null_rows.append((str(market_id), qtext, category))
    if max_rows is not None:
        null_rows = null_rows[:max_rows]

    LOGGER.info(
        "reconcile-all: %d rows to embed (skipped %d empty-question rows)",
        len(null_rows),
        skipped_empty,
    )

    stats = {
        "to_embed": len(null_rows),
        "embedded": 0,
        "skipped_empty": skipped_empty,
        "dry_run": int(dry_run),
    }

    if dry_run or not null_rows:
        return stats

    # --- Load the existing FAISS index + meta ---------------------------- #
    index = faiss.read_index(str(index_path))
    meta_payload = json.loads(meta_path.read_text())
    meta_records = list(meta_payload.get("records", []))
    existing_count = index.ntotal
    LOGGER.info(
        "loaded existing index: ntotal=%d, meta_records=%d",
        existing_count,
        len(meta_records),
    )

    # Lazy-load the encoder once.
    if model is None:
        from sentence_transformers import SentenceTransformer  # type: ignore

        model = SentenceTransformer(model_name)

    # --- Embed in batches and append to the index ------------------------ #
    embedded = 0
    for start in range(0, len(null_rows), batch_size):
        batch = null_rows[start : start + batch_size]
        texts = [row[1] for row in batch]
        vecs = embed_texts(texts, model_name=model_name, model=model)
        if vecs.dtype != np.float32:
            vecs = vecs.astype("float32")
        # Position of the FIRST new vector in the appended block.
        base_idx = index.ntotal
        index.add(vecs)

        # Update DB rows + meta records to match the new indices.
        with session_scope() as session:
            for offset, (market_id, qtext, category) in enumerate(batch):
                new_idx = base_idx + offset
                row = session.get(CorpusMarket, market_id)
                if row is None:
                    continue
                row.embedding_idx = int(new_idx)
                meta_records.append(
                    {
                        "idx": int(new_idx),
                        "market_id": str(market_id),
                        "question": qtext,
                        "category": str(category or ""),
                    }
                )
                embedded += 1

        LOGGER.info(
            "batch %d-%d: appended %d vectors (ntotal=%d)",
            start,
            start + len(batch),
            len(batch),
            index.ntotal,
        )

    # --- Persist the grown index + meta sidecar -------------------------- #
    faiss.write_index(index, str(index_path))
    meta_path.write_text(
        json.dumps({"records": meta_records}, ensure_ascii=False, indent=2)
    )
    LOGGER.info(
        "reconcile-all complete: %d embedded, index ntotal=%d, meta=%d",
        embedded,
        index.ntotal,
        len(meta_records),
    )

    stats["embedded"] = embedded
    return stats


# --------------------------------------------------------------------------- #
# CLI.                                                                        #
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parquet", default="corpus/polymarket_questions.parquet"
    )
    parser.add_argument("--index", default=str(DEFAULT_INDEX_PATH))
    parser.add_argument("--meta", default=str(DEFAULT_META_PATH))
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING")
    )
    parser.add_argument(
        "--reconcile-all",
        action="store_true",
        help=(
            "Embed every CorpusMarket row whose embedding_idx is NULL and"
            " append to the existing FAISS index."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_RECONCILE_BATCH_SIZE,
        help="Reconcile batch size (rows per embed call).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report what would be embedded; do not modify the index.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional hard cap on rows reconciled (for smoke tests).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.reconcile_all:
        stats = reconcile_all(
            index_path=Path(args.index),
            meta_path=Path(args.meta),
            batch_size=args.batch_size,
            model_name=args.model,
            dry_run=args.dry_run,
            max_rows=args.max_rows,
        )
        LOGGER.info("reconcile-all stats: %s", stats)
        return 0

    build_corpus_index(
        Path(args.parquet),
        index_path=Path(args.index),
        meta_path=Path(args.meta),
        model_name=args.model,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
