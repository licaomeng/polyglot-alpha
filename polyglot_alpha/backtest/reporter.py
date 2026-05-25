"""Generate the Markdown backtest report.

Pure formatting — takes already-computed :class:`BacktestResult` and
summary dicts and produces a ~2-page operator-facing recap.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Sequence

from .roi_estimator import BUILDER_FEE_BPS

_ROI_BUCKETS: tuple[tuple[float, str], ...] = (
    (0.0, "0"),
    (1.0, "<1"),
    (10.0, "1-10"),
    (100.0, "10-100"),
    (1000.0, "100-1000"),
    (float("inf"), "1000+"),
)


def _roi_bucket(value: float) -> str:
    for upper, label in _ROI_BUCKETS:
        if value < upper or upper == float("inf"):
            return label
    return _ROI_BUCKETS[-1][1]


def _safe_div(num: float, den: float) -> float:
    if den == 0:
        return 0.0
    return num / den


def _format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render a tiny GitHub-flavoured Markdown table."""

    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(cells) + " |" for cells in rows)
    return "\n".join([head, sep, body]) if rows else "\n".join([head, sep])


def generate_report(results: Sequence[Any], summary: dict[str, Any]) -> str:
    """Return the full Markdown report text."""

    n = summary.get("n_markets", 0)
    if n == 0:
        return "# PolyglotAlpha v2 Backtest Report\n\n_No markets backtested._\n"

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Executive numbers.
    accuracy = summary.get("outcome_accuracy", 0.0) * 100
    sim_avg = summary.get("semantic_similarity_avg", 0.0)
    roi_total = summary.get("estimated_total_roi_usdc", 0.0)
    roi_avg = _safe_div(roi_total, n)
    n_pass = summary.get("n_PASS", 0)
    n_fail = summary.get("n_FAIL", 0)
    n_border = summary.get("n_BORDERLINE", 0)
    n_error = summary.get("n_ERROR", 0)

    # ROI histogram.
    roi_hist: Counter[str] = Counter(_roi_bucket(r.estimated_roi_usdc) for r in results)
    roi_rows = [
        [bucket, str(roi_hist.get(bucket, 0))]
        for _, bucket in _ROI_BUCKETS
    ]

    # Best wins and worst misses.
    sorted_by_roi = sorted(results, key=lambda r: r.estimated_roi_usdc, reverse=True)
    best_wins = sorted_by_roi[:5]
    worst_misses = [
        r for r in sorted(results, key=lambda r: (r.outcome_match, r.estimated_roi_usdc))
        if not r.outcome_match
    ][:5]

    # Category table.
    per_cat = summary.get("per_category", {})
    cat_rows = []
    for cat, data in sorted(per_cat.items(), key=lambda kv: kv[1]["n"], reverse=True):
        cat_rows.append([
            cat,
            str(data["n"]),
            f"{data['accuracy'] * 100:.1f}%",
            f"{data['pass_rate'] * 100:.1f}%",
            f"${data['roi']:,.2f}",
        ])

    # D5 scorecard.
    uma_total = summary.get("uma_dispute_total", 0)
    uma_caught = summary.get("uma_dispute_caught_by_D5", 0)
    uma_missed = summary.get("uma_dispute_missed_by_D5", 0)
    d5_recall = _safe_div(uma_caught, uma_total) * 100 if uma_total else 0.0

    # Reputation calibration: agents on failing markets vs passing ones.
    agent_perf: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = agent_perf.setdefault(
            r.agent_winner or "unknown", {"wins": 0, "passes": 0, "fails": 0}
        )
        bucket["wins"] += 1
        if r.judge_verdict == "PASS":
            bucket["passes"] += 1
        elif r.judge_verdict == "FAIL":
            bucket["fails"] += 1

    rep_rows = []
    for agent, stats in sorted(agent_perf.items()):
        wins = stats["wins"] or 1
        pass_rate = stats["passes"] / wins * 100
        fail_rate = stats["fails"] / wins * 100
        recommendation = "slash" if fail_rate > 50 else ("boost" if pass_rate > 70 else "hold")
        rep_rows.append([
            agent,
            str(stats["wins"]),
            f"{pass_rate:.1f}%",
            f"{fail_rate:.1f}%",
            recommendation,
        ])

    # Build sections.
    sections: list[str] = []
    sections.append("# PolyglotAlpha v2 Backtest Report")
    sections.append("")
    sections.append(f"_Generated: {now}_")
    sections.append("")
    sections.append("## Executive summary")
    sections.append("")
    sections.append(
        f"- **Markets backtested**: {n}\n"
        f"- **Outcome accuracy**: {accuracy:.1f}% (agent framing matched actual resolution)\n"
        f"- **Mean semantic similarity**: {sim_avg:.3f} (sentence-transformers cosine)\n"
        f"- **Hypothetical total ROI**: ${roi_total:,.2f} "
        f"(builder-fee = {BUILDER_FEE_BPS:.0f} bps)\n"
        f"- **Mean ROI per market**: ${roi_avg:,.2f}\n"
        f"- **Judge panel**: {n_pass} PASS / {n_border} BORDERLINE / {n_fail} FAIL"
        f" / {n_error} ERROR\n"
        f"- **UMA disputes**: D5 caught {uma_caught}/{uma_total} ({d5_recall:.1f}% recall)"
    )
    sections.append("")

    sections.append("## Accuracy by category")
    sections.append("")
    sections.append(
        _format_table(
            ["Category", "N", "Accuracy", "Pass-rate", "ROI"],
            cat_rows or [["—", "—", "—", "—", "—"]],
        )
    )
    sections.append("")

    sections.append("## ROI distribution")
    sections.append("")
    sections.append(
        _format_table(["Bucket (USDC)", "Count"], roi_rows)
    )
    sections.append("")

    sections.append("## Top 5 wins (highest ROI)")
    sections.append("")
    if best_wins:
        win_rows = [
            [
                r.market_id,
                r.category or "—",
                r.judge_verdict,
                f"${r.estimated_roi_usdc:,.2f}",
                (r.agent_question or "")[:80],
            ]
            for r in best_wins
        ]
        sections.append(
            _format_table(["market_id", "category", "verdict", "ROI", "agent_question"], win_rows)
        )
    else:
        sections.append("_(none)_")
    sections.append("")

    sections.append("## Top 5 misses (incorrect outcome)")
    sections.append("")
    if worst_misses:
        miss_rows = [
            [
                r.market_id,
                r.category or "—",
                r.actual_outcome or "—",
                r.framing_predicted or "—",
                (r.actual_question or "")[:80],
            ]
            for r in worst_misses
        ]
        sections.append(
            _format_table(
                ["market_id", "category", "actual", "predicted", "actual_question"],
                miss_rows,
            )
        )
    else:
        sections.append("_(none — all framings matched)_")
    sections.append("")

    sections.append("## D5 dispute-detection scorecard")
    sections.append("")
    sections.append(
        f"- Disputes in sample: {uma_total}\n"
        f"- Caught by D5 (FAIL on dispute markets): {uma_caught}\n"
        f"- Missed by D5 (PASS on dispute markets): {uma_missed}\n"
        f"- D5 recall: {d5_recall:.1f}%"
    )
    sections.append("")

    sections.append("## Reputation calibration recommendation")
    sections.append("")
    sections.append(
        "Recommendations are simple-majority over backtested winners; see README §5.22 "
        "for the production EWMA rule."
    )
    sections.append("")
    sections.append(
        _format_table(
            ["Agent", "Wins", "Pass-rate", "Fail-rate", "Recommendation"],
            rep_rows or [["—", "—", "—", "—", "—"]],
        )
    )
    sections.append("")

    sections.append("## Methodology notes")
    sections.append("")
    sections.append(
        "- Trigger events are reverse-engineered from the historical question text;\n"
        "  the agent pipeline runs against a synthetic Chinese summary rather than\n"
        "  the original news article.\n"
        f"- ROI assumes a {BUILDER_FEE_BPS:.0f} bps builder fee, with capture rate\n"
        "  determined by the panel verdict (PASS@high-conf=30%, PASS=10%, BORDERLINE=2%, FAIL=0%).\n"
        "- Outcome match is heuristic: we infer YES/NO framing from question phrasing\n"
        "  and compare against the actual resolution. Non-binary outcomes are skipped."
    )
    sections.append("")

    return "\n".join(sections)
