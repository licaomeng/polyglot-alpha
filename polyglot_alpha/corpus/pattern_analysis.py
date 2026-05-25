"""Classify each Polymarket question into one of six framing patterns.

The patterns are matched in priority order — the first regex that fires
wins. Order matters because, for example, ``"How many X by Y?"`` (P6)
also matches ``"Will X by Y?"`` only superficially, so we test P6 first.

Pattern legend:

    P1 "Will X by [date]?"            — classic deadline-bounded YES/NO
    P2 noun-phrase multi-outcome      — "Next president?", "Winner of X?"
                                        (lacks a verb-leading "Will" but
                                        ends with a question mark)
    P3 "[Asset] above [threshold]"    — price/threshold questions
    P4 "Who will be the next X?"      — open-ended "who" questions
    P5 "Will X happen between A and B?" — window-bounded YES/NO
    P6 "How many X by [date]?"         — count-by-date questions
"""
from __future__ import annotations

import argparse
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

LOGGER = logging.getLogger(__name__)

PATTERN_LABELS = ("P1", "P2", "P3", "P4", "P5", "P6", "OTHER")

EXPECTED_DISTRIBUTION = {
    "P1": 0.45,
    "P2": 0.20,
    "P3": 0.15,
    "P4": 0.10,
    "P5": 0.10,
    "P6": 0.05,
}

# Compiled regexes — keep the order in sync with `classify_pattern`.
_RE_P6_HOW_MANY = re.compile(
    r"^\s*how\s+many\b.*?\bby\b.*\?\s*$", re.IGNORECASE
)
_RE_P5_BETWEEN = re.compile(
    r"^\s*will\b.*\bbetween\b.*\band\b.*\?\s*$", re.IGNORECASE
)
_RE_P4_WHO_NEXT = re.compile(
    r"^\s*who\s+(?:will\s+)?(?:be|win|become)\b.*\bnext\b.*\?\s*$",
    re.IGNORECASE,
)
_RE_P4_WHO_FALLBACK = re.compile(r"^\s*who\b.*\?\s*$", re.IGNORECASE)
_RE_P3_THRESHOLD = re.compile(
    r"(above|below|reach|exceed|hit|cross|over|under|"
    r"greater than|less than|>=?|<=?)\s*\$?\d",
    re.IGNORECASE,
)
_RE_P1_WILL_BY = re.compile(
    r"^\s*will\b.*\b(by|before|on|in|prior to|by the end of)\b.*\?\s*$",
    re.IGNORECASE,
)
_RE_P1_WILL_GENERIC = re.compile(r"^\s*will\b.*\?\s*$", re.IGNORECASE)
_RE_QUESTION_MARK = re.compile(r"\?\s*$")


@dataclass(frozen=True)
class PatternStats:
    """Counts + percentages for the corpus-wide pattern distribution."""

    counts: dict[str, int]
    total: int

    def percentages(self) -> dict[str, float]:
        if self.total == 0:
            return {label: 0.0 for label in PATTERN_LABELS}
        return {
            label: 100.0 * self.counts.get(label, 0) / self.total
            for label in PATTERN_LABELS
        }


def classify_pattern(question: str) -> str:
    """Return the label of the first pattern that matches the question."""

    if not question:
        return "OTHER"

    # Order matters — P6 first because it can be mistaken for P1.
    if _RE_P6_HOW_MANY.search(question):
        return "P6"
    if _RE_P5_BETWEEN.search(question):
        return "P5"
    if _RE_P4_WHO_NEXT.search(question):
        return "P4"
    if _RE_P3_THRESHOLD.search(question):
        return "P3"
    if _RE_P1_WILL_BY.search(question):
        return "P1"
    if _RE_P1_WILL_GENERIC.search(question):
        return "P1"
    if _RE_P4_WHO_FALLBACK.search(question):
        return "P4"
    if _RE_QUESTION_MARK.search(question):
        # Noun-phrase / leftover question with no leading verb -> P2.
        return "P2"
    return "OTHER"


def classify_dataframe(
    df: pd.DataFrame, *, question_col: str = "question"
) -> pd.Series:
    """Add a series of pattern labels for every row of ``df``."""

    return df[question_col].astype(str).map(classify_pattern)


def summarize_patterns(labels: Iterable[str]) -> PatternStats:
    counter: Counter[str] = Counter(labels)
    total = sum(counter.values())
    counts = {label: counter.get(label, 0) for label in PATTERN_LABELS}
    return PatternStats(counts=counts, total=total)


def stats_to_report(stats: PatternStats) -> str:
    """Render a Markdown summary contrasting actual and expected shares."""

    pcts = stats.percentages()
    lines = [
        "# Polymarket Question Framing Patterns",
        "",
        f"Sample size: **{stats.total}** binary questions.",
        "",
        "| Pattern | Description | Actual % | Expected % | Delta |",
        "|---|---|---|---|---|",
    ]
    descriptions = {
        "P1": "Will X by [date]?",
        "P2": "Noun-phrase multi-outcome",
        "P3": "[Asset] above [threshold]",
        "P4": "Who will be the next X?",
        "P5": "Will X happen between [start] and [end]?",
        "P6": "How many X by [date]?",
        "OTHER": "Unclassified",
    }
    for label in PATTERN_LABELS:
        actual = pcts.get(label, 0.0)
        expected = EXPECTED_DISTRIBUTION.get(label)
        if expected is None:
            expected_str = "—"
            delta_str = "—"
        else:
            expected_pct = expected * 100
            expected_str = f"{expected_pct:.1f}%"
            delta_str = f"{actual - expected_pct:+.1f} pp"
        lines.append(
            f"| {label} | {descriptions[label]} | "
            f"{actual:.1f}% | {expected_str} | {delta_str} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "- Classification is regex-based; ties resolve to the highest-priority "
            "pattern (P6 > P5 > P4 > P3 > P1 > P2).",
            "- `OTHER` captures malformed or non-question rows (typos, missing "
            "trailing `?`, etc.).",
            "- Threshold detection (P3) requires a numeric literal after a "
            "comparison word so we don't mislabel ordinary `will-by-date` "
            "questions that mention prices in passing.",
        ]
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI.                                                                        #
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parquet", default="corpus/polymarket_questions.parquet"
    )
    parser.add_argument("--out", default="corpus/patterns_report.md")
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
    labels = classify_dataframe(df)
    stats = summarize_patterns(labels)
    report = stats_to_report(stats)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    LOGGER.info("wrote pattern report -> %s", out_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
