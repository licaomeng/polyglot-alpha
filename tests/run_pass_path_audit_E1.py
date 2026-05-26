"""E1 stress audit — 20 PASS-path events across 5 variations.

Based on tests/run_pass_path_audit.py (A1). Adds:
  * Configurable mock_bids per variation
  * Per-phase wall-clock timestamps from pub_events
  * Bid winner verification (vs. orchestrator's
    bid_amount / max(reputation, 1.0) scoring rule)
  * Outputs land in outputs/E1_stress_audit/
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import _pass_path_mocks as _mocks_mod  # noqa: E402
from tests._pass_path_mocks import install_mocks, uninstall_mocks  # noqa: E402
# Re-use the harness primitives from A1's audit so we stay byte-compatible.
from tests.run_pass_path_audit import (  # noqa: E402
    _AuditSink,
    _dump_event_rows,
    _winner_address,
    PER_EVENT_TIMEOUT_S,
    BASE_TITLE,
)

logging.basicConfig(
    level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
)

OUTPUT_DIR: Path = ROOT / "outputs" / "E1_stress_audit"

# ---------------------------------------------------------------------------
# Variation specs — 5 variations x 4 events = 20
# ---------------------------------------------------------------------------

# Stable 40-hex chunk helpers
def _h(c: str) -> str:
    return "0x" + (c * 40)[:40]


VARIATIONS: list[dict[str, Any]] = [
    {
        "name": "V1_three_qualified",
        "bids": [
            {"addr": "operator", "bid": 0.50, "rep": 0.85, "stake": 5.0},
            {"addr": _h("b"), "bid": 0.30, "rep": 0.92, "stake": 5.0},
            {"addr": _h("c"), "bid": 0.75, "rep": 0.75, "stake": 5.0},
        ],
        "expected_winner_idx": 1,  # bid=0.30, qualified, lowest score
    },
    {
        "name": "V2_mixed_qualified",
        "bids": [
            {"addr": "operator", "bid": 0.50, "rep": 0.95, "stake": 5.0},
            {"addr": _h("d"), "bid": 0.20, "rep": 0.6, "stake": 5.0},  # UNQUAL
            {"addr": _h("e"), "bid": 0.55, "rep": 0.8, "stake": 5.0},
        ],
        # Unqualified rep=0.6 < 0.7 filtered out; among {0.50@.95, 0.55@.80}
        # winner = min(bid/max(rep,1.0)) = min(0.50/1.0, 0.55/1.0) = bid=0.50
        "expected_winner_idx": 0,
    },
    {
        "name": "V3_solo_bid",
        "bids": [
            {"addr": "operator", "bid": 0.40, "rep": 0.85, "stake": 5.0},
        ],
        "expected_winner_idx": 0,
    },
    {
        "name": "V4_tie_break",
        "bids": [
            {"addr": "operator", "bid": 0.50, "rep": 0.85, "stake": 5.0},
            {"addr": _h("f"), "bid": 0.50, "rep": 0.90, "stake": 5.0},
        ],
        # Both same bid+score; ``min`` picks first occurrence -> idx 0
        "expected_winner_idx": 0,
    },
    {
        "name": "V5_five_bids",
        "bids": [
            {"addr": "operator", "bid": 0.60, "rep": 0.80, "stake": 5.0},
            {"addr": _h("a"), "bid": 0.40, "rep": 0.95, "stake": 5.0},
            {"addr": _h("b"), "bid": 0.35, "rep": 0.85, "stake": 5.0},
            {"addr": _h("c"), "bid": 0.55, "rep": 0.78, "stake": 5.0},
            {"addr": _h("d"), "bid": 0.45, "rep": 0.90, "stake": 5.0},
        ],
        # All qualified, lowest bid = 0.35 (idx 2)
        "expected_winner_idx": 2,
    },
]

EVENTS_PER_VARIATION: int = 4
NUM_EVENTS: int = len(VARIATIONS) * EVENTS_PER_VARIATION  # 20


def _make_bid_records(variation: dict[str, Any]) -> tuple[list[Any], int, str]:
    """Translate the variation spec to BidRecord objects.

    Returns (bid_records, expected_winner_idx, expected_winner_addr).
    The literal string ``operator`` is resolved to the actual operator
    wallet so the on-chain split path fires.
    """

    from polyglot_alpha.orchestrator import BidRecord

    op = _winner_address()
    records: list[Any] = []
    for spec in variation["bids"]:
        addr = op if spec["addr"] == "operator" else spec["addr"]
        records.append(
            BidRecord(
                agent_address=addr,
                bid_amount=float(spec["bid"]),
                stake_amount=float(spec["stake"]),
                candidate_hash=None,
                tx_hash=None,
                reputation=float(spec["rep"]),
            )
        )
    idx = int(variation["expected_winner_idx"])
    return records, idx, records[idx].agent_address


def _compute_expected_winner(bids: list[Any]) -> tuple[int, str]:
    """Re-implement the orchestrator's ``_settle_auction`` ranking rule."""
    MIN_REP = 0.7
    qualified_indices = [i for i, b in enumerate(bids) if b.reputation >= MIN_REP]
    pool = qualified_indices or list(range(len(bids)))
    # ``min`` ties go to the first occurrence
    winner_local_i = min(
        pool, key=lambda i: bids[i].bid_amount / max(bids[i].reputation, 1.0)
    )
    return winner_local_i, bids[winner_local_i].agent_address


async def _run_one(
    idx: int, variation: dict[str, Any], sink: _AuditSink
) -> dict[str, Any]:
    """Drive one PASS-path lifecycle with the given variation."""

    from polyglot_alpha.orchestrator import run_lifecycle

    salt = uuid.uuid4().hex[:8]
    title = f"{BASE_TITLE} [E1-{variation['name']}-{idx}-{salt}]"
    event_dict: dict[str, Any] = {
        "title": title,
        "sources": [
            {
                "name": "audit-source",
                "url": f"https://audit.example/E1/{salt}",
                "language": "en",
            }
        ],
        "language": "en",
        "category": "macro",
        "summary": (
            "E1 stress audit synthetic source for variation "
            f"{variation['name']}."
        ),
    }

    bid_records, declared_winner_idx, declared_winner_addr = _make_bid_records(
        variation
    )
    computed_winner_idx, computed_winner_addr = _compute_expected_winner(
        bid_records
    )

    sink.reset()
    t0 = time.monotonic()
    summary: dict[str, Any] = {}
    try:
        summary = await asyncio.wait_for(
            run_lifecycle(
                event_dict,
                auction_window_seconds=0.0,
                mock_bids=bid_records,
                auction_mode="mock",
                confirm_real_polymarket=False,
            ),
            timeout=PER_EVENT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        summary = {
            "status": "TIMEOUT",
            "error": f"run_lifecycle exceeded {PER_EVENT_TIMEOUT_S:.0f}s",
        }
    wallclock = time.monotonic() - t0

    event_id = summary.get("event_id")
    if event_id is None:
        return {
            "audit_index": idx,
            "variation": variation["name"],
            "title": title,
            "wallclock_s": wallclock,
            "summary": summary,
            "error": "event_id missing from run_lifecycle result",
            "expected_winner": {
                "declared_index": declared_winner_idx,
                "declared_addr": declared_winner_addr,
                "computed_index": computed_winner_idx,
                "computed_addr": computed_winner_addr,
            },
        }

    rows = _dump_event_rows(int(event_id))
    pub_events = sink.for_event(int(event_id))

    # First-seen timestamps per phase
    phases_seen: dict[str, str] = {}
    for ev in pub_events:
        phases_seen.setdefault(ev["topic"], ev["timestamp"])

    # Per-phase wall-clock (seconds since the first event)
    phase_deltas: dict[str, float] = {}
    if pub_events:
        first_ts = datetime.fromisoformat(pub_events[0]["timestamp"])
        for topic, ts in phases_seen.items():
            phase_deltas[topic] = (
                datetime.fromisoformat(ts) - first_ts
            ).total_seconds()

    # Topic ordering
    sse_topic_order = [ev["topic"] for ev in pub_events]

    polymarket_row = rows["polymarket_submissions"][0] if rows[
        "polymarket_submissions"
    ] else None
    question_row = rows["questions"][0] if rows["questions"] else None
    fee_rows = rows["builder_fee_events"]
    quality_row = rows["quality_scores"][0] if rows["quality_scores"] else None
    auction_row = rows["auctions"][0] if rows["auctions"] else None
    event_row = rows["events"][0] if rows["events"] else None

    fee_amounts = sorted(float(f.get("fee_amount") or 0.0) for f in fee_rows)
    fee_total = sum(fee_amounts) if fee_amounts else 0.0
    has_split_90 = any(abs(a - 0.9) < 1e-6 for a in fee_amounts)
    has_split_10 = any(abs(a - 0.1) < 1e-6 for a in fee_amounts)

    # Determine actual winner from auctions row (winning_bid_id maps to a bid)
    actual_winner_addr: str | None = None
    actual_winner_bid_id: Any = None
    if auction_row is not None:
        actual_winner_bid_id = auction_row.get("winning_bid_id")
        for b in rows["bids"]:
            if b.get("id") == actual_winner_bid_id:
                actual_winner_addr = b.get("agent_address")
                break
    # Fallback: scan question_row for winner_address
    if actual_winner_addr is None and question_row is not None:
        actual_winner_addr = question_row.get("winner_address") or question_row.get(
            "writer_address"
        )

    subsystem_status = {
        "rss_bypassed": True,
        "db_event_written": event_row is not None,
        "auction_opened": any(
            ev["topic"] == "auction.opened" for ev in pub_events
        ),
        "submit_bid_count": sum(
            1 for ev in pub_events if ev["topic"] == "bid.submitted"
        ),
        "auction_settled": auction_row is not None
        and auction_row.get("settlement_tx_hash") is not None,
        "translation_persisted": len(rows["translations"]) > 0,
        "judges_pass_verdict": (
            quality_row is not None and quality_row.get("verdict") == "PASS"
        ),
        "commit_question_persisted": question_row is not None
        and question_row.get("question_id_onchain") is not None,
        "commit_tx_hash_nonnull": question_row is not None
        and question_row.get("tx_hash") is not None,
        "polymarket_submitted": polymarket_row is not None
        and polymarket_row.get("market_id") is not None,
        "builder_fee_split_present": len(fee_rows) >= 2,
        "builder_fee_90_leg": has_split_90,
        "builder_fee_10_leg": has_split_10,
        "builder_fee_total_1usdc": abs(fee_total - 1.0) < 1e-6,
        "reputation_updated": any(
            (r.get("agent_address") == actual_winner_addr)
            and (
                (r.get("total_wins") or 0) >= 1
                or float(r.get("cumulative_fees") or 0) > 0
            )
            for r in rows["agent_reputation"]
        ),
    }

    bid_audit = {
        "expected_winner_addr": computed_winner_addr,
        "expected_winner_idx": computed_winner_idx,
        "declared_winner_addr": declared_winner_addr,
        "declared_winner_idx": declared_winner_idx,
        "actual_winner_addr": actual_winner_addr,
        "actual_winner_bid_id": actual_winner_bid_id,
        "match_with_computed_rule": (
            actual_winner_addr is not None
            and actual_winner_addr.lower() == computed_winner_addr.lower()
        ),
        "bids": [
            {
                "agent_address": b.agent_address,
                "bid_amount": b.bid_amount,
                "reputation": b.reputation,
                "qualified": b.reputation >= 0.7,
                "score": b.bid_amount / max(b.reputation, 1.0),
            }
            for b in bid_records
        ],
    }

    tx_hashes = {
        "open_auction": summary.get("open_tx_hash"),
        "settle_auction": (
            auction_row.get("settlement_tx_hash") if auction_row else None
        ),
        "submit_bids": [b.get("tx_hash") for b in rows["bids"]],
        "commit_question": (
            question_row.get("tx_hash") if question_row else None
        ),
        "builder_fee_arc": [f.get("arc_tx_hash") for f in fee_rows],
    }

    return {
        "audit_index": idx,
        "variation": variation["name"],
        "event_id": event_id,
        "title": title,
        "wallclock_s": wallclock,
        "summary": summary,
        "phase_timestamps": phases_seen,
        "phase_deltas_s": phase_deltas,
        "sse_topic_order": sse_topic_order,
        "sse_event_count": len(pub_events),
        "subsystem_status": subsystem_status,
        "bid_audit": bid_audit,
        "tx_hashes": tx_hashes,
        "db_rows": rows,
        "pub_events": pub_events,
        "correlation_id": f"E1-{idx}-{salt}",
    }


async def _main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    install_mocks()
    sink = _AuditSink()
    sink.install()

    audits: list[dict[str, Any]] = []
    overall_t0 = time.monotonic()
    try:
        global_idx = 0
        for variation in VARIATIONS:
            for sub in range(EVENTS_PER_VARIATION):
                global_idx += 1
                print(
                    f"[E1] === {global_idx}/{NUM_EVENTS} variation="
                    f"{variation['name']} sub={sub+1}/{EVENTS_PER_VARIATION} ==="
                )
                audit = await _run_one(global_idx, variation, sink)
                audits.append(audit)
                event_id = audit.get("event_id", "?")
                status = (audit.get("summary") or {}).get("status", "?")
                wc = audit.get("wallclock_s", 0.0)
                bid_match = audit.get("bid_audit", {}).get(
                    "match_with_computed_rule"
                )
                print(
                    f"[E1] event_id={event_id} status={status} wallclock={wc:.2f}s "
                    f"winner_rule_match={bid_match}",
                    flush=True,
                )
                if isinstance(event_id, int):
                    out_path = OUTPUT_DIR / f"audit_event_{event_id}.json"
                    out_path.write_text(json.dumps(audit, indent=2, default=str))
    finally:
        sink.uninstall()
        uninstall_mocks()

    overall = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "wallclock_total_s": time.monotonic() - overall_t0,
        "event_count": len(audits),
        "panel_evaluate_calls": _mocks_mod.panel_evaluate_calls,
        "mock_llm_calls": _mocks_mod.mock_llm_calls,
        "submitted_count": sum(
            1
            for a in audits
            if (a.get("summary") or {}).get("status") == "SUBMITTED"
        ),
        "variations_summary": [
            {
                "audit_index": a["audit_index"],
                "variation": a.get("variation"),
                "event_id": a.get("event_id"),
                "status": (a.get("summary") or {}).get("status"),
                "wallclock_s": a.get("wallclock_s"),
                "subsystem_status": a.get("subsystem_status"),
                "bid_match": a.get("bid_audit", {}).get(
                    "match_with_computed_rule"
                ),
                "sse_event_count": a.get("sse_event_count"),
            }
            for a in audits
        ],
    }

    summary_path = OUTPUT_DIR / "E1_audit_summary.json"
    summary_path.write_text(json.dumps(overall, indent=2, default=str))
    print(f"[E1] wrote {summary_path}")

    submitted = overall["submitted_count"]
    print(f"\n[E1] SUBMITTED {submitted}/{len(audits)}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
