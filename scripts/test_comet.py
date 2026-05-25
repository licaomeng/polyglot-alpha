"""Trigger the first COMET model download + score one (src, mt) pair.

Run with the project venv:
    .venv/bin/python scripts/test_comet.py
"""

from __future__ import annotations

import time

from comet import download_model, load_from_checkpoint

# Per spec we prefer Unbabel/wmt22-cometkiwi-da (reference-free), but that
# model is a *gated* HF repo and the locally-stored HF token lacks
# ``canReadGatedRepos`` permission. Fall back to the non-gated WMT20 QE
# DA-v2 model (also reference-free) so the judge can run end-to-end.
PREFERRED_MODEL = "Unbabel/wmt22-cometkiwi-da"
FALLBACK_MODEL = "Unbabel/wmt20-comet-qe-da"


def main() -> None:
    MODEL_ID = PREFERRED_MODEL
    print(f"[test_comet] trying preferred {MODEL_ID} (gated HF)...")
    t0 = time.perf_counter()
    try:
        ckpt_path = download_model(MODEL_ID)
    except Exception as exc:  # noqa: BLE001 - we want broad fallback
        print(f"[test_comet] preferred failed: {type(exc).__name__}: {exc}")
        MODEL_ID = FALLBACK_MODEL
        print(f"[test_comet] falling back to {MODEL_ID} (non-gated)...")
        t0 = time.perf_counter()
        ckpt_path = download_model(MODEL_ID)
    print(f"[test_comet] checkpoint at: {ckpt_path}")
    print(f"[test_comet] download/locate took {time.perf_counter() - t0:.2f}s")

    print("[test_comet] loading model from checkpoint...")
    t1 = time.perf_counter()
    model = load_from_checkpoint(ckpt_path)
    print(f"[test_comet] load took {time.perf_counter() - t1:.2f}s")

    data = [
        {
            "src": "央行行长潘功胜在金融街论坛年会上表示，将根据需要适时降准。",
            "mt": (
                "Will the People's Bank of China (PBOC) announce a cut to the "
                "Reserve Requirement Ratio (RRR) before August 23, 2026?"
            ),
        }
    ]
    print("[test_comet] running predict on 1 pair (CPU)...")
    t2 = time.perf_counter()
    output = model.predict(data, batch_size=1, progress_bar=False, gpus=0)
    elapsed = time.perf_counter() - t2

    system_score = getattr(output, "system_score", None)
    scores = getattr(output, "scores", None)
    print(f"[test_comet] system_score = {system_score}")
    print(f"[test_comet] scores       = {scores}")
    print(f"[test_comet] predict took {elapsed:.2f}s")


if __name__ == "__main__":
    main()
