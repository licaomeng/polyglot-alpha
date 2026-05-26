"""Backfill canonical per-judge keys into ``quality_scores`` JSON columns.

W13-B Phase 4 closure. Historical rows persisted before this wave have
``translation_scores`` keyed by the raw panel emissions (``bleu``,
``comet``, ``mqm``) and ``style_alignment_passes`` keyed by the short
``d1`` .. ``d8`` aliases. The W12 audit flagged these as missing the
canonical judge names (``mqm_llm``, ``d1_structural`` .. ``d8_duplicate_detection``).

This script walks every ``quality_scores`` row and, for each row,
derives the canonical name -> score/passed mapping from the existing
``translation_scores._judges`` dossier (W9-A smuggle). Rows that
predate the dossier emission are tagged with ``backfill_unknown=True``
under both columns so consumers can distinguish "never had a dossier"
from "dossier present, canonical keys derived".

The script is idempotent: rows that already carry the canonical keys
are skipped (no rewrite). Safe to re-run.

Usage::

    .venv/bin/python scripts/backfill_qualityscore_json.py

Add ``--dry-run`` to preview the change set without writing.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

# Canonical translation judge names. Order must match
# ``judges/panel.py::_MOCK_JUDGE_NAMES`` for the first three entries.
_TRANSLATION_CANONICAL: tuple[str, ...] = ("bleu", "comet", "mqm_llm")

# Short alias -> canonical full name for the 8 style judges.
_STYLE_CANONICAL_BY_SHORT: dict[str, str] = {
    "d1": "d1_structural",
    "d2": "d2_stylistic",
    "d3": "d3_framing",
    "d4": "d4_granularity",
    "d5": "d5_resolution_clarity",
    "d6": "d6_source_reliability",
    "d7": "d7_leading_check",
    "d8": "d8_duplicate_detection",
}


def _derive_canonical(
    translation_scores: dict[str, Any],
    style_alignment_passes: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Return updated copies + a ``changed`` flag.

    Strategy:
      * Prefer the ``_judges`` dossier when present (carries the
        canonical names + normalized 0-1 ``score`` field).
      * Fallback: map short ``d1``..``d8`` aliases to their canonical
        names by hardcoded lookup; for ``mqm_llm`` synthesize from the
        existing ``mqm`` dict's ``score`` (0-100 -> 0-1).
      * Last-resort: stamp ``backfill_unknown`` so consumers know the
        row could not be enriched.
    """

    ts_out = dict(translation_scores)
    sap_out = dict(style_alignment_passes)
    changed = False

    dossier = translation_scores.get("_judges") if isinstance(
        translation_scores, dict
    ) else None

    if isinstance(dossier, list) and dossier:
        for j in dossier:
            if not isinstance(j, dict):
                continue
            raw_name = j.get("name")
            if not isinstance(raw_name, str):
                continue
            # Legacy live-verify rows wrote uppercase names (BLEU/COMET/MQM).
            # Normalize to lowercase so the canonical-name lookup matches.
            name_lower = raw_name.lower()
            # Map legacy ``mqm`` (without _llm suffix) onto the canonical
            # judge name. The dossier score is on the 0-1 scale (or 0-100 for
            # legacy rows that smuggled the raw MQM); both fall through to
            # the float conversion below — the consumer normalizes display.
            if name_lower == "mqm":
                name_lower = "mqm_llm"
            if name_lower in _TRANSLATION_CANONICAL:
                raw = j.get("score")
                if name_lower not in ts_out and isinstance(raw, (int, float)):
                    score = float(raw)
                    # Legacy uppercase MQM rows stored 0-100 directly; map
                    # to 0-1 so all canonical translation scores share scale.
                    if name_lower == "mqm_llm" and score > 1:
                        score = score / 100.0
                    ts_out[name_lower] = score
                    changed = True
            elif name_lower.startswith("d") and name_lower not in sap_out:
                sap_out[name_lower] = bool(j.get("passed"))
                changed = True
        return ts_out, sap_out, changed

    # No dossier — try direct alias mapping for style passes.
    for short, full in _STYLE_CANONICAL_BY_SHORT.items():
        if short in sap_out and full not in sap_out:
            sap_out[full] = bool(sap_out[short])
            changed = True

    # mqm_llm fallback from the panel-emitted ``mqm`` dict.
    if "mqm_llm" not in ts_out:
        mqm_raw = ts_out.get("mqm")
        if isinstance(mqm_raw, dict):
            mqm_score = mqm_raw.get("score")
            if isinstance(mqm_score, (int, float)):
                # 0-100 -> 0-1 normalized.
                normalized = float(mqm_score) / 100.0 if mqm_score > 1 else float(
                    mqm_score
                )
                ts_out["mqm_llm"] = normalized
                changed = True

    # If we still couldn't fill in any canonical translation key, tag
    # the row so consumers can flag it instead of treating absence as
    # "judge didn't run".
    has_any_canonical_ts = any(k in ts_out for k in _TRANSLATION_CANONICAL)
    has_any_canonical_sap = any(
        full in sap_out for full in _STYLE_CANONICAL_BY_SHORT.values()
    )
    if not has_any_canonical_ts and "backfill_unknown" not in ts_out:
        ts_out["backfill_unknown"] = True
        changed = True
    if not has_any_canonical_sap and "backfill_unknown" not in sap_out:
        sap_out["backfill_unknown"] = True
        changed = True

    return ts_out, sap_out, changed


def _row_already_canonical(
    ts: dict[str, Any], sap: dict[str, Any]
) -> bool:
    """Return True when both columns already carry canonical keys."""

    ts_has_all = all(k in ts for k in _TRANSLATION_CANONICAL)
    sap_has_all = all(
        full in sap for full in _STYLE_CANONICAL_BY_SHORT.values()
    )
    return ts_has_all and sap_has_all


def backfill(db_path: Path, dry_run: bool = False) -> dict[str, int]:
    """Walk every quality_scores row, derive canonical keys, write back.

    Returns a stats dict: ``{"total", "already_canonical", "updated",
    "tagged_unknown", "no_change"}``.
    """

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT event_id, translation_scores, style_alignment_passes "
            "FROM quality_scores"
        ).fetchall()
    finally:
        # we'll re-open for writes below
        pass

    stats = {
        "total": len(rows),
        "already_canonical": 0,
        "updated": 0,
        "tagged_unknown": 0,
        "no_change": 0,
    }

    updates: list[tuple[int, str, str]] = []
    for event_id, ts_raw, sap_raw in rows:
        try:
            ts_obj: dict[str, Any] = json.loads(ts_raw) if ts_raw else {}
        except (TypeError, json.JSONDecodeError):
            ts_obj = {}
        try:
            sap_obj: dict[str, Any] = json.loads(sap_raw) if sap_raw else {}
        except (TypeError, json.JSONDecodeError):
            sap_obj = {}

        if _row_already_canonical(ts_obj, sap_obj):
            stats["already_canonical"] += 1
            continue

        new_ts, new_sap, changed = _derive_canonical(ts_obj, sap_obj)
        if not changed:
            stats["no_change"] += 1
            continue

        if new_ts.get("backfill_unknown") or new_sap.get("backfill_unknown"):
            stats["tagged_unknown"] += 1
        else:
            stats["updated"] += 1

        updates.append(
            (event_id, json.dumps(new_ts), json.dumps(new_sap))
        )

    if not dry_run and updates:
        with conn:
            conn.executemany(
                "UPDATE quality_scores SET translation_scores = ?, "
                "style_alignment_passes = ? WHERE event_id = ?",
                [(ts, sap, eid) for eid, ts, sap in updates],
            )
    conn.close()
    return stats


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("polyglot_alpha.db"),
        help="Path to the SQLite database (default: polyglot_alpha.db).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Audit and print stats without writing.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    if not args.db.exists():
        print(f"error: database not found: {args.db}", file=sys.stderr)
        return 1

    stats = backfill(args.db, dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] backfill_qualityscore_json on {args.db}")
    print(f"  total rows:         {stats['total']}")
    print(f"  already canonical:  {stats['already_canonical']}")
    print(f"  updated (dossier):  {stats['updated']}")
    print(f"  tagged unknown:     {stats['tagged_unknown']}")
    print(f"  no-change:          {stats['no_change']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
