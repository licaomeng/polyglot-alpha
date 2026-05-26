#!/usr/bin/env python3
"""D8 health check — verify SBert model + FAISS index are usable.

Run with the project venv (no FastAPI / backend required):

    .venv/bin/python scripts/check_d8_health.py

Exit code 0 if all components are healthy, 1 otherwise. Intended as a
pre-flight check before kicking off a long live-mode run — pairs with
the startup pre-warm logged under ``d8.model_load:`` in
``logs/backend.*.log`` (see W13-D).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Make the local package importable when running the script directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _cache_dir_for_model(model_id: str) -> Path:
    """Return the HF hub cache directory that would back ``model_id``."""

    hf_home = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    slug = "models--" + model_id.replace("/", "--")
    return hf_home / "hub" / slug


def main() -> int:
    print("D8 health check")
    print("-" * 17)

    from polyglot_alpha.judges.style_alignment import d8_duplicate_detection as d8

    failures: list[str] = []

    # ----- 1. SBert model -----
    model_id = d8.DEFAULT_MODEL_ID
    cache_path = _cache_dir_for_model(model_id)
    cache_existed_before = cache_path.exists()
    t0 = time.perf_counter()
    model = d8._load_embedding_model()
    elapsed = time.perf_counter() - t0
    if model is None:
        err = d8.get_last_model_load_error() or "unknown"
        print(f"SBert model: {model_id} ... FAILED ({err})")
        failures.append(f"sbert: {err}")
    else:
        source = "cache" if cache_existed_before else "download"
        print(
            f"SBert model: {model_id} ... LOADED from {source} "
            f"({cache_path}) in {elapsed:.2f}s"
        )

    # ----- 2. FAISS index -----
    index_path = d8.DEFAULT_INDEX_PATH
    if not index_path.is_absolute():
        index_path = _REPO_ROOT / index_path
    t0 = time.perf_counter()
    index = d8._load_index(index_path)
    elapsed = time.perf_counter() - t0
    if index is None:
        exists = index_path.exists()
        reason = "file missing" if not exists else "faiss read failed"
        print(f"FAISS index: {index_path} ... FAILED ({reason})")
        failures.append(f"faiss: {reason}")
    else:
        ntotal = getattr(index, "ntotal", -1)
        ndim = getattr(index, "d", -1)
        print(
            f"FAISS index: {index_path} ... LOADED "
            f"({ntotal} vectors, {ndim}-dim) in {elapsed:.2f}s"
        )

    # ----- 3. End-to-end embed test -----
    if model is not None and index is not None:
        try:
            import numpy as np  # noqa: F401 - imported for dtype check below

            t0 = time.perf_counter()
            emb = model.encode(
                ["Will BTC close above $100k by 2026-12-31?"],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            shape = getattr(emb, "shape", None)
            dtype = getattr(emb, "dtype", None)
            elapsed = time.perf_counter() - t0
            print(
                f"Test embed: shape={shape} dtype={dtype} OK "
                f"({elapsed:.3f}s)"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Test embed: FAILED ({type(exc).__name__}: {exc})")
            failures.append(f"embed: {exc}")

    print()
    if failures:
        print("D8 STATUS: UNHEALTHY")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("D8 STATUS: HEALTHY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
