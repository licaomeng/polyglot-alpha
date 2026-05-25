"""Build a 50-question few-shots JSON for in-context learning.

We pick high-volume, diverse exemplars across categories. The selection
algorithm is two-stage:

  1. Bucket the corpus by ``category`` (lowercased, normalised).
  2. From each bucket, take the top-N rows by lifetime ``volume_usd``
     until we hit ``target_count`` total exemplars.

Each emitted record includes a one-sentence ``why_good_exemplar`` rationale
that explains what makes the question canonical Polymarket-style — that
field is what downstream prompt templates concatenate into the system
message.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

LOGGER = logging.getLogger(__name__)

DEFAULT_TARGET = 50
PREFERRED_CATEGORIES = (
    "politics",
    "sports",
    "crypto",
    "geopolitics",
    "entertainment",
    "weather",
    "macro",
    "economy",
    "tech",
    "ai",
    "science",
    "elections",
)
FALLBACK_BUCKET = "_other"


@dataclass(frozen=True)
class FewShot:
    """One few-shot exemplar."""

    title: str
    category: str
    resolution_criteria: str
    why_good_exemplar: str
    market_id: str


def _normalize_category(raw: object) -> str:
    if not isinstance(raw, str):
        return FALLBACK_BUCKET
    cat = raw.strip().lower()
    if not cat:
        return FALLBACK_BUCKET
    return cat


def _why_good(question: str, category: str) -> str:
    """Build a short rationale string.

    We pick from a handful of templates depending on which framing
    pattern the question matches — this is what gives the few-shots a
    self-documenting feel when injected into a prompt.
    """

    q_lower = question.lower()
    if "by " in q_lower and q_lower.startswith("will"):
        return (
            "Crisp YES/NO frame with explicit deadline — a textbook "
            "Polymarket structure for category "
            f"'{category}'."
        )
    if "between" in q_lower and q_lower.startswith("will"):
        return (
            "Bounded-window YES/NO that fixes both lower and upper "
            "resolution edges."
        )
    if q_lower.startswith("how many"):
        return (
            "Count-by-date framing — quantifiable threshold rather than "
            "an open-ended estimate."
        )
    if q_lower.startswith("who"):
        return (
            "Open-set 'who' framing requiring a named individual at "
            "resolution time."
        )
    if any(kw in q_lower for kw in (" above ", " below ", " reach ")):
        return (
            "Asset-threshold framing — numeric criterion that resolves "
            "against a public price feed."
        )
    return (
        "Concise, unambiguous Polymarket-style question with a binary "
        "outcome and an objective resolution source."
    )


def _select_rows(df: pd.DataFrame, target_count: int) -> pd.DataFrame:
    """Pick ``target_count`` diverse high-volume exemplars."""

    if df.empty:
        return df

    df = df.copy()
    df["_cat"] = df["category"].map(_normalize_category)
    df["_vol"] = pd.to_numeric(df.get("volume_usd", 0), errors="coerce").fillna(0)

    selected_ids: set[str] = set()
    picked_rows: list[pd.Series] = []
    bucket_quota_per_round = 1

    bucket_order = list(PREFERRED_CATEGORIES) + sorted(
        c for c in df["_cat"].unique() if c not in PREFERRED_CATEGORIES
    )

    # Pre-sort each bucket once.
    bucket_dfs: dict[str, pd.DataFrame] = {}
    for cat in bucket_order:
        sub = df[df["_cat"] == cat]
        if not sub.empty:
            bucket_dfs[cat] = sub.sort_values("_vol", ascending=False)

    while len(picked_rows) < target_count and bucket_dfs:
        progressed = False
        for cat in list(bucket_dfs.keys()):
            if len(picked_rows) >= target_count:
                break
            sub = bucket_dfs[cat]
            taken_here = 0
            for _, row in sub.iterrows():
                if str(row["market_id"]) in selected_ids:
                    continue
                picked_rows.append(row)
                selected_ids.add(str(row["market_id"]))
                taken_here += 1
                progressed = True
                if taken_here >= bucket_quota_per_round:
                    break
                if len(picked_rows) >= target_count:
                    break
            if taken_here == 0:
                # Bucket exhausted relative to selected set.
                del bucket_dfs[cat]
        if not progressed:
            break

    return pd.DataFrame(picked_rows)


def build_few_shots(df: pd.DataFrame, target_count: int = DEFAULT_TARGET) -> list[FewShot]:
    selected = _select_rows(df, target_count)
    out: list[FewShot] = []
    for _, row in selected.iterrows():
        title = str(row.get("question") or "").strip()
        if not title:
            continue
        category = str(row.get("category") or "").strip() or "uncategorized"
        criteria = str(row.get("resolution_criteria") or "").strip()
        out.append(
            FewShot(
                title=title,
                category=category,
                resolution_criteria=criteria[:1000],
                why_good_exemplar=_why_good(title, category),
                market_id=str(row.get("market_id") or ""),
            )
        )
    return out


def save_few_shots(few_shots: list[FewShot], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "count": len(few_shots),
        "examples": [asdict(fs) for fs in few_shots],
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return dest


# --------------------------------------------------------------------------- #
# CLI.                                                                        #
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parquet", default="corpus/polymarket_questions.parquet"
    )
    parser.add_argument("--out", default="corpus/few_shots.json")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET)
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING")
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    df = pd.read_parquet(args.parquet)
    few = build_few_shots(df, target_count=args.target)
    out = save_few_shots(few, Path(args.out))
    LOGGER.info("wrote %d few-shot examples -> %s", len(few), out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
