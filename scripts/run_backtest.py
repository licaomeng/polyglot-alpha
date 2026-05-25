"""CLI for the PolyglotAlpha v2 backtest framework.

Usage:

    python scripts/run_backtest.py --n 100 --random-seed 42 \\
        --out outputs/backtest/ --mock-llm

    python scripts/run_backtest.py --n 20 --real-llm

Set ``--real-llm`` only if ``GEMINI_API_KEY`` or ``OPENROUTER_API_KEY``
is in the environment (the LLM layer falls back to the deterministic
``MockLLM`` if no key is set).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Allow running this file directly without an editable install.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from polyglot_alpha.backtest.runner import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RESOLVED_PARQUET,
    run_backtest,
)

LOGGER = logging.getLogger("polyglot_alpha.backtest.cli")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=100, help="Number of markets to backtest.")
    parser.add_argument(
        "--random-seed", type=int, default=42, help="Seed for reproducibility."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for results (default: outputs/backtest/).",
    )
    parser.add_argument(
        "--resolved-parquet",
        type=Path,
        default=DEFAULT_RESOLVED_PARQUET,
        help=f"Path to resolved markets parquet (default: {DEFAULT_RESOLVED_PARQUET}).",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--mock-llm",
        action="store_true",
        default=True,
        help="Use deterministic mock LLM (default; fast).",
    )
    mode.add_argument(
        "--real-llm",
        action="store_false",
        dest="mock_llm",
        help="Use real LLM via existing make_llm() (slower; needs API key).",
    )
    parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="Force the deterministic Jaccard similarity fallback.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable INFO logging.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    started = time.time()
    summary = run_backtest(
        n=args.n,
        seed=args.random_seed,
        output_dir=args.out,
        mock_llm=args.mock_llm,
        use_embeddings=not args.no_embeddings,
        parquet_path=args.resolved_parquet,
    )
    elapsed = time.time() - started

    print(
        f"Backtest done in {elapsed:.1f}s | n={summary.get('n_markets', 0)} | "
        f"accuracy={summary.get('outcome_accuracy', 0) * 100:.1f}% | "
        f"ROI=${summary.get('estimated_total_roi_usdc', 0):,.2f} | "
        f"PASS={summary.get('n_PASS', 0)} FAIL={summary.get('n_FAIL', 0)} "
        f"BORDERLINE={summary.get('n_BORDERLINE', 0)}"
    )
    print(f"Artifacts: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
