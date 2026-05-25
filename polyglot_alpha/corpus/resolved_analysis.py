"""Compute statistics and human-readable summary from the resolved-markets corpus.

This is the post-processing step run after ``resolved_scraper`` writes
``corpus/polymarket_resolved.parquet``. It emits two artifacts:

* ``corpus/outcome_distribution.json`` — machine-readable distributions
  (YES/NO/disputed by category and by volume tier, overall dispute rate)
* ``corpus/resolved_summary.md`` — human-readable summary with sample
  markets, top-volume table, and UMA dispute case studies

Both files are committed to git; the parquet/csv/jsonl are not.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

LOGGER = logging.getLogger(__name__)

# Outcome buckets used in the by-category breakdown.
_YES = "YES"
_NO = "NO"
_DISPUTED = "DISPUTED"

# Volume tier boundaries (in USDC) — chosen to roughly trisect the
# realistic-volume tail; markets with $0 volume are excluded.
_LOW_TIER_MAX = 1_000.0
_MID_TIER_MAX = 100_000.0


def _bucket_outcome(outcome: str) -> str:
    """Map raw outcome to {yes, no, disputed, other}."""

    norm = outcome.strip().lower()
    if norm == "yes":
        return "yes"
    if norm == "no":
        return "no"
    if norm in ("disputed", "refunded"):
        return "disputed"
    return "other"


def _volume_tier(volume: float) -> str:
    if volume < _LOW_TIER_MAX:
        return "low"
    if volume < _MID_TIER_MAX:
        return "mid"
    return "high"


def compute_distribution(df: pd.DataFrame) -> dict[str, Any]:
    """Build the dict written to ``outcome_distribution.json``."""

    df = df.copy()
    df["_bucket"] = df["outcome"].apply(_bucket_outcome)
    df["_tier"] = df["total_volume_usdc"].apply(_volume_tier)

    by_category: dict[str, dict[str, int]] = {}
    for category, sub in df.groupby("category", dropna=False):
        counts = sub["_bucket"].value_counts().to_dict()
        by_category[str(category) or "Other"] = {
            "yes": int(counts.get("yes", 0)),
            "no": int(counts.get("no", 0)),
            "disputed": int(counts.get("disputed", 0)),
            "other": int(counts.get("other", 0)),
            "total": int(len(sub)),
            "uma_dispute_rate": float(sub["uma_dispute"].mean()),
        }

    by_volume_tier: dict[str, dict[str, Any]] = {}
    for tier, sub in df.groupby("_tier"):
        counts = sub["_bucket"].value_counts().to_dict()
        by_volume_tier[tier] = {
            "yes": int(counts.get("yes", 0)),
            "no": int(counts.get("no", 0)),
            "disputed": int(counts.get("disputed", 0)),
            "other": int(counts.get("other", 0)),
            "total": int(len(sub)),
            "uma_dispute_rate": float(sub["uma_dispute"].mean()),
            "volume_usdc_sum": float(sub["total_volume_usdc"].sum()),
        }

    return {
        "total_markets": int(len(df)),
        "yes_rate_overall": float((df["_bucket"] == "yes").mean()),
        "no_rate_overall": float((df["_bucket"] == "no").mean()),
        "disputed_rate_overall": float((df["_bucket"] == "disputed").mean()),
        "uma_dispute_rate_overall": float(df["uma_dispute"].mean()),
        "by_category": by_category,
        "by_volume_tier": by_volume_tier,
        "volume_tier_boundaries_usdc": {
            "low_max": _LOW_TIER_MAX,
            "mid_max": _MID_TIER_MAX,
        },
    }


def _md_table_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _format_volume(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:.0f}"


def _truncate(s: str, n: int = 80) -> str:
    s = s.replace("\n", " ").replace("|", "\\|").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def build_summary_markdown(df: pd.DataFrame, dist: dict[str, Any]) -> str:
    """Render the human-readable summary as markdown."""

    total = dist["total_markets"]
    yes_pct = dist["yes_rate_overall"] * 100
    no_pct = dist["no_rate_overall"] * 100
    disputed_pct = dist["disputed_rate_overall"] * 100
    uma_pct = dist["uma_dispute_rate_overall"] * 100

    # Top categories by total volume.
    cat_volume = (
        df.groupby("category")["total_volume_usdc"].sum().sort_values(ascending=False)
    )
    top_categories_lines = []
    for cat, vol in cat_volume.head(8).items():
        cat_label = cat or "Other"
        top_categories_lines.append(f"- **{cat_label}** — {_format_volume(vol)}")

    # 20 sample markets — most recent first (already sorted that way).
    sample_lines = [
        _md_table_row(["question", "category", "volume", "outcome", "disputed"]),
        _md_table_row(["---", "---", "---", "---", "---"]),
    ]
    for _, r in df.head(20).iterrows():
        sample_lines.append(
            _md_table_row(
                [
                    _truncate(str(r["question"]), 72),
                    str(r["category"] or "Other"),
                    _format_volume(float(r["total_volume_usdc"])),
                    str(r["outcome"]),
                    "yes" if bool(r["uma_dispute"]) else "no",
                ]
            )
        )

    # UMA dispute case studies — top 8 disputed markets by volume.
    disputed_top = (
        df[df["uma_dispute"]]
        .sort_values("total_volume_usdc", ascending=False)
        .head(8)
    )
    dispute_lines = []
    if disputed_top.empty:
        dispute_lines.append("_(No disputed markets in this corpus snapshot.)_")
    else:
        for _, r in disputed_top.iterrows():
            dispute_lines.append(
                f"- **{_truncate(str(r['question']), 90)}** — "
                f"{str(r['category'] or 'Other')}, "
                f"{_format_volume(float(r['total_volume_usdc']))} volume, "
                f"outcome `{r['outcome']}`"
            )

    # Top 10 markets by volume.
    top_volume = df.nlargest(10, "total_volume_usdc")
    top_volume_lines = [
        _md_table_row(["#", "question", "category", "volume", "outcome", "uma"]),
        _md_table_row(["---", "---", "---", "---", "---", "---"]),
    ]
    for i, (_, r) in enumerate(top_volume.iterrows(), start=1):
        top_volume_lines.append(
            _md_table_row(
                [
                    str(i),
                    _truncate(str(r["question"]), 70),
                    str(r["category"] or "Other"),
                    _format_volume(float(r["total_volume_usdc"])),
                    str(r["outcome"]),
                    "yes" if bool(r["uma_dispute"]) else "no",
                ]
            )
        )

    # Per-category breakdown.
    cat_lines = [
        _md_table_row(
            ["category", "total", "YES", "NO", "DISPUTED", "uma_rate"]
        ),
        _md_table_row(["---", "---", "---", "---", "---", "---"]),
    ]
    for cat in sorted(dist["by_category"], key=lambda c: -dist["by_category"][c]["total"]):
        stats = dist["by_category"][cat]
        cat_lines.append(
            _md_table_row(
                [
                    cat,
                    str(stats["total"]),
                    str(stats["yes"]),
                    str(stats["no"]),
                    str(stats["disputed"]),
                    f"{stats['uma_dispute_rate']*100:.1f}%",
                ]
            )
        )

    parts = [
        "# Polymarket Resolved Markets — Ground Truth",
        "",
        "Snapshot of closed/resolved binary markets pulled from the Polymarket "
        "Gamma API, ordered by `endDate` descending. Used by PolyglotAlpha "
        "v2 for backtesting the 4-agent system, calibrating judge reputation, "
        "and validating the D5 dispute-detection signal.",
        "",
        "## Stats",
        "",
        f"- **Total markets**: {total:,}",
        f"- **YES resolution**: {yes_pct:.1f}%",
        f"- **NO resolution**: {no_pct:.1f}%",
        f"- **DISPUTED / REFUNDED**: {disputed_pct:.1f}%",
        f"- **UMA dispute trace present**: {uma_pct:.2f}% "
        f"(critical signal for D5 — any market whose UMA status "
        f"transitions include `disputed` went through at least one "
        f"oracle challenge cycle)",
        "",
        "## Top categories by lifetime volume",
        "",
        *top_categories_lines,
        "",
        "## Per-category outcome breakdown",
        "",
        *cat_lines,
        "",
        "## 20 sample markets (most recently ended)",
        "",
        *sample_lines,
        "",
        "## Top 10 by volume",
        "",
        *top_volume_lines,
        "",
        "## UMA dispute case studies (top 8 disputed markets by volume)",
        "",
        *dispute_lines,
        "",
        "## Notes on data quality",
        "",
        "- The deprecated Gamma `/markets` endpoint caps offsets around 10k "
        "and returns at most 100 markets per page; pagination is offset-"
        "based with `order=endDate, ascending=false`.",
        "- The `umaResolutionStatuses` field is the canonical dispute "
        "trace; we set `uma_dispute=true` whenever the JSON array "
        "contains a `\"disputed\"` token.",
        "- `category` is empty in most newer market payloads. When raw "
        "category is missing we derive a coarse label from question + "
        "event-title keyword matching; markets with no keyword match are "
        "bucketed as `Other`.",
        "- Non-Yes/No binary markets (e.g. team-vs-team sports props) "
        "expose the literal winning team label in `outcome` so backtest "
        "can still score them.",
    ]
    return "\n".join(parts) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parquet", default="corpus/polymarket_resolved.parquet"
    )
    parser.add_argument(
        "--out-json", default="corpus/outcome_distribution.json"
    )
    parser.add_argument(
        "--out-md", default="corpus/resolved_summary.md"
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING")
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    df = pd.read_parquet(args.parquet)
    LOGGER.info("loaded %d rows from %s", len(df), args.parquet)

    distribution = compute_distribution(df)
    markdown = build_summary_markdown(df, distribution)

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(distribution, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(markdown, encoding="utf-8")

    LOGGER.info("wrote %s and %s", out_json, out_md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
