"""L2 stress audit — 30 PASS-path events across 6 variations.

Builds on E1's runner. Variations target K1-introduced regression
surface (single-provider consolidation: Anthropic-only LLM path):

  V1: 3-bid standard PASS                       (5 events)
  V2: 5-bid 高竞争 PASS (close-spaced bids)      (5 events)
  V3: 2-bid edge                                (5 events)
  V4: low-reputation gate (mix qual+unqual)     (5 events)
  V5: long titles (200-char Chinese/Arabic/emoji) (5 events)
  V6: rapid-fire (no yield between)             (5 events)

Outputs land in outputs/L2_stress_30/.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import statistics
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
from tests.run_pass_path_audit import (  # noqa: E402
    _AuditSink,
    _dump_event_rows,
    _winner_address,
    PER_EVENT_TIMEOUT_S,
)
from tests.run_pass_path_audit_E1 import _compute_expected_winner  # noqa: E402

logging.basicConfig(
    level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
)

OUTPUT_DIR: Path = ROOT / "outputs" / "L2_stress_30"

BASE_TITLE_EN = "Will the FOMC raise rates by 25bp at the June 2026 meeting?"


def _h(c: str) -> str:
    return "0x" + (c * 40)[:40]


# Long titles for V5 — 200-char Chinese / Arabic / emoji + ASCII anchor
LONG_TITLE_VARIANTS = [
    # Chinese — repeated narrative, ~200 chars after trimming
    (
        "宏观经济观察：美联储二零二六年六月十七至十八日会议加息二十五个基点的"
        "概率是否超过五成？市场预期回顾、点阵图分析、近期通胀数据与就业报告"
        "综合评估，叠加联邦基金期货隐含路径与官员讲话偏鹰偏鸽对比研究讨论。"
        "FOMC-2026-06"
    ),
    # Arabic
    (
        "هل سيرفع مجلس الاحتياطي الفيدرالي أسعار الفائدة بمقدار خمسة وعشرين"
        " نقطة أساس خلال اجتماع السابع عشر والثامن عشر من يونيو عام ألفين"
        " وستة وعشرين؟ تحليل توقعات السوق والبيانات الاقتصادية الأخيرة"
        " مع مراجعة شاملة لخطابات المسؤولين وموقف اللجنة الفيدرالية."
    ),
    # Emoji heavy
    (
        "🇺🇸💵📈 Will the FOMC 🏦 raise rates ⬆️ by 25bp 💯 at the June 🌞 2026"
        " meeting? 🗓️🤔 Forecast 🔮 from market pricing 💹, Fed dot-plot 📊,"
        " CPI 🧾, NFP 👷, and FOMC speakers 🎤 — synthesized 🧪🔬 for the"
        " end-user 👤. Tag: macro-2026-06-fomc-decision-watch 🚨🎯✨"
    ),
    # Mixed CJK + ASCII technical
    (
        "Macro预测：[FOMC-2026-06] 议息会议是否加息25基点（25bp）？参考CME"
        "FedWatch 概率、SEP点阵图（Summary of Economic Projections）、最新"
        "PCE/CPI/PPI通胀面板、JOLTS与NFP劳动力市场指标，以及Powell主席与各"
        "Voting Members的近期讲话立场综合判断。"
    ),
    # Devanagari (Hindi)
    (
        "क्या यू.एस. फेडरल रिज़र्व जून 2026 की मौद्रिक नीति समिति की बैठक में"
        " ब्याज दरों में 25 आधार अंकों की वृद्धि करेगा? बाजार की अपेक्षाएं,"
        " डॉट प्लॉट विश्लेषण, मुद्रास्फीति के आंकड़े और श्रम बाजार के"
        " संकेतकों का व्यापक अध्ययन। FOMC-2026-06 निर्णय निगरानी।"
    ),
]


# ---------------------------------------------------------------------------
# 6 variations × 5 events = 30
# ---------------------------------------------------------------------------

VARIATIONS: list[dict[str, Any]] = [
    {
        # V1: standard 3-bid PASS
        "name": "V1_standard_3bid",
        "events": 5,
        "rapid_fire": False,
        "long_title_idx": None,
        "bids": [
            {"addr": "operator", "bid": 0.50, "rep": 0.85, "stake": 5.0},
            {"addr": _h("b"), "bid": 0.30, "rep": 0.92, "stake": 5.0},
            {"addr": _h("c"), "bid": 0.75, "rep": 0.75, "stake": 5.0},
        ],
    },
    {
        # V2: 5-bid 高竞争 — closely-spaced bids 0.30, 0.31, 0.32, 0.33, 0.34
        "name": "V2_close_spaced_5bid",
        "events": 5,
        "rapid_fire": False,
        "long_title_idx": None,
        "bids": [
            {"addr": "operator", "bid": 0.34, "rep": 0.85, "stake": 5.0},
            {"addr": _h("a"), "bid": 0.33, "rep": 0.85, "stake": 5.0},
            {"addr": _h("b"), "bid": 0.32, "rep": 0.85, "stake": 5.0},
            {"addr": _h("c"), "bid": 0.31, "rep": 0.85, "stake": 5.0},
            {"addr": _h("d"), "bid": 0.30, "rep": 0.85, "stake": 5.0},
        ],
    },
    {
        # V3: 2-bid edge
        "name": "V3_two_bid_edge",
        "events": 5,
        "rapid_fire": False,
        "long_title_idx": None,
        "bids": [
            {"addr": "operator", "bid": 0.50, "rep": 0.90, "stake": 5.0},
            {"addr": _h("e"), "bid": 0.40, "rep": 0.85, "stake": 5.0},
        ],
    },
    {
        # V4: low-reputation gate — 2 qualified, 2 unqualified
        # Qualified: operator rep=0.85, _h("g") rep=0.9
        # Unqualified: _h("f") rep=0.5, _h("h") rep=0.6
        "name": "V4_low_rep_gate",
        "events": 5,
        "rapid_fire": False,
        "long_title_idx": None,
        "bids": [
            {"addr": "operator", "bid": 0.60, "rep": 0.85, "stake": 5.0},
            {"addr": _h("f"), "bid": 0.20, "rep": 0.5, "stake": 5.0},
            {"addr": _h("g"), "bid": 0.55, "rep": 0.9, "stake": 5.0},
            {"addr": _h("h"), "bid": 0.25, "rep": 0.6, "stake": 5.0},
        ],
    },
    {
        # V5: long titles per-event (cycle through LONG_TITLE_VARIANTS)
        "name": "V5_long_unicode_titles",
        "events": 5,
        "rapid_fire": False,
        "long_title_idx": "cycle",
        "bids": [
            {"addr": "operator", "bid": 0.50, "rep": 0.85, "stake": 5.0},
            {"addr": _h("i"), "bid": 0.40, "rep": 0.90, "stake": 5.0},
            {"addr": _h("j"), "bid": 0.60, "rep": 0.80, "stake": 5.0},
        ],
    },
    {
        # V6: rapid-fire — fire 5 events back-to-back via asyncio.gather
        "name": "V6_rapid_fire",
        "events": 5,
        "rapid_fire": True,
        "long_title_idx": None,
        "bids": [
            {"addr": "operator", "bid": 0.50, "rep": 0.85, "stake": 5.0},
            {"addr": _h("k"), "bid": 0.40, "rep": 0.90, "stake": 5.0},
            {"addr": _h("l"), "bid": 0.60, "rep": 0.80, "stake": 5.0},
        ],
    },
]


NUM_EVENTS: int = sum(v["events"] for v in VARIATIONS)
assert NUM_EVENTS == 30


def _make_bid_records(variation: dict[str, Any]) -> list[Any]:
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
    return records


def _build_event_dict(
    variation: dict[str, Any], idx: int, sub_idx: int, salt: str
) -> tuple[dict[str, Any], str]:
    """Build event_dict and return (event_dict, title_used)."""

    if variation["long_title_idx"] == "cycle":
        base = LONG_TITLE_VARIANTS[sub_idx % len(LONG_TITLE_VARIANTS)]
        title = f"{base} [L2-{variation['name']}-{idx}-{salt}]"
    else:
        title = (
            f"{BASE_TITLE_EN} [L2-{variation['name']}-{idx}-{salt}]"
        )
    event_dict: dict[str, Any] = {
        "title": title,
        "sources": [
            {
                "name": "audit-source",
                "url": f"https://audit.example/L2/{salt}",
                "language": "en",
            }
        ],
        "language": "en",
        "category": "macro",
        "summary": (
            f"L2 stress audit synthetic source for variation "
            f"{variation['name']}."
        ),
    }
    return event_dict, title


def _build_audit_from_summary(
    idx: int,
    variation_name: str,
    title: str,
    salt: str,
    summary: dict[str, Any],
    pub_events: list[dict[str, Any]],
    bid_records: list[Any],
    wallclock: float,
) -> dict[str, Any]:
    event_id = summary.get("event_id")
    computed_winner_idx, computed_winner_addr = _compute_expected_winner(
        bid_records
    )

    if event_id is None:
        return {
            "audit_index": idx,
            "variation": variation_name,
            "title": title,
            "wallclock_s": wallclock,
            "summary": summary,
            "error": "event_id missing from run_lifecycle result",
        }

    rows = _dump_event_rows(int(event_id))

    phases_seen: dict[str, str] = {}
    for ev in pub_events:
        phases_seen.setdefault(ev["topic"], ev["timestamp"])

    phase_deltas: dict[str, float] = {}
    if pub_events:
        first_ts = datetime.fromisoformat(pub_events[0]["timestamp"])
        for topic, ts in phases_seen.items():
            phase_deltas[topic] = (
                datetime.fromisoformat(ts) - first_ts
            ).total_seconds()

    sse_topic_order = [ev["topic"] for ev in pub_events]

    polymarket_row = (
        rows["polymarket_submissions"][0]
        if rows["polymarket_submissions"]
        else None
    )
    question_row = rows["questions"][0] if rows["questions"] else None
    fee_rows = rows["builder_fee_events"]
    quality_row = rows["quality_scores"][0] if rows["quality_scores"] else None
    auction_row = rows["auctions"][0] if rows["auctions"] else None
    event_row = rows["events"][0] if rows["events"] else None

    fee_amounts = sorted(float(f.get("fee_amount") or 0.0) for f in fee_rows)
    fee_total = sum(fee_amounts) if fee_amounts else 0.0
    has_split_90 = any(abs(a - 0.9) < 1e-6 for a in fee_amounts)
    has_split_10 = any(abs(a - 0.1) < 1e-6 for a in fee_amounts)

    actual_winner_addr: str | None = None
    actual_winner_bid_id: Any = None
    if auction_row is not None:
        # Schema-of-truth: auctions.winner_address (denormalized).
        actual_winner_addr = auction_row.get("winner_address")
        # Try also winning_bid_id for backwards-compat with older E1 snapshots.
        actual_winner_bid_id = auction_row.get("winning_bid_id")
        if actual_winner_addr is None and actual_winner_bid_id is not None:
            for b in rows["bids"]:
                if b.get("id") == actual_winner_bid_id:
                    actual_winner_addr = b.get("agent_address")
                    break
    if actual_winner_addr is None and question_row is not None:
        actual_winner_addr = question_row.get(
            "winner_address"
        ) or question_row.get("writer_address")

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

    # Title metrics — for V5 check Unicode preservation
    title_metrics = {
        "title_db": event_row.get("title") if event_row else None,
        "title_len_chars": len(title),
        "title_db_len_chars": len(event_row.get("title") or "")
        if event_row
        else 0,
        "title_round_trip_ok": (event_row is not None)
        and (event_row.get("title") == title),
    }

    return {
        "audit_index": idx,
        "variation": variation_name,
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
        "title_metrics": title_metrics,
        "db_rows": rows,
        "pub_events": pub_events,
        "correlation_id": f"L2-{idx}-{salt}",
    }


async def _run_one(
    idx: int,
    variation: dict[str, Any],
    sub_idx: int,
    sink: _AuditSink,
) -> dict[str, Any]:
    from polyglot_alpha.orchestrator import run_lifecycle

    salt = uuid.uuid4().hex[:8]
    event_dict, title = _build_event_dict(variation, idx, sub_idx, salt)
    bid_records = _make_bid_records(variation)

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
    pub_events = (
        sink.for_event(int(event_id)) if event_id is not None else []
    )

    return _build_audit_from_summary(
        idx=idx,
        variation_name=variation["name"],
        title=title,
        salt=salt,
        summary=summary,
        pub_events=pub_events,
        bid_records=bid_records,
        wallclock=wallclock,
    )


async def _run_rapid_fire(
    base_idx: int,
    variation: dict[str, Any],
    sink: _AuditSink,
) -> list[dict[str, Any]]:
    """Trigger N events back-to-back via asyncio.gather.

    LIFECYCLE_MAX_CONCURRENCY=1 means the orchestrator semaphore should
    serialize them. We do NOT reset the sink between sub-events; instead
    we capture the full sink and dispatch per-event_id at the end.
    """
    from polyglot_alpha.orchestrator import run_lifecycle

    n = int(variation["events"])
    salts = [uuid.uuid4().hex[:8] for _ in range(n)]
    titles: list[str] = []
    event_dicts: list[dict[str, Any]] = []
    bid_records_list: list[list[Any]] = []
    for sub in range(n):
        ev, tt = _build_event_dict(variation, base_idx + sub, sub, salts[sub])
        event_dicts.append(ev)
        titles.append(tt)
        bid_records_list.append(_make_bid_records(variation))

    # Note: do NOT reset sink — we capture all rapid-fire events together
    sink.reset()
    t0 = time.monotonic()

    async def _one(ev: dict[str, Any], br: list[Any]) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                run_lifecycle(
                    ev,
                    auction_window_seconds=0.0,
                    mock_bids=br,
                    auction_mode="mock",
                    confirm_real_polymarket=False,
                ),
                timeout=PER_EVENT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return {
                "status": "TIMEOUT",
                "error": f"run_lifecycle exceeded {PER_EVENT_TIMEOUT_S:.0f}s",
            }

    summaries = await asyncio.gather(
        *[_one(event_dicts[i], bid_records_list[i]) for i in range(n)]
    )
    total_wallclock = time.monotonic() - t0

    # Bucket pub_events by event_id; per-event wallclock derived from
    # pub_event timestamps (first to last) since they all interleave.
    audits: list[dict[str, Any]] = []
    for sub in range(n):
        summ = summaries[sub]
        event_id = summ.get("event_id")
        pub_events = (
            sink.for_event(int(event_id)) if event_id is not None else []
        )
        # per-event wallclock = last - first pub_event timestamp
        if pub_events:
            first = datetime.fromisoformat(pub_events[0]["timestamp"])
            last = datetime.fromisoformat(pub_events[-1]["timestamp"])
            per_wallclock = (last - first).total_seconds()
        else:
            per_wallclock = float("nan")
        audit = _build_audit_from_summary(
            idx=base_idx + sub,
            variation_name=variation["name"],
            title=titles[sub],
            salt=salts[sub],
            summary=summ,
            pub_events=pub_events,
            bid_records=bid_records_list[sub],
            wallclock=per_wallclock,
        )
        audit["rapid_fire_total_wallclock_s"] = total_wallclock
        audit["rapid_fire_sub_idx"] = sub
        audits.append(audit)

    return audits


# ---------------------------------------------------------------------------
# Timing aggregation
# ---------------------------------------------------------------------------


def _aggregate_timing(audits: list[dict[str, Any]]) -> dict[str, Any]:
    by_var: dict[str, list[float]] = {}
    all_wc: list[float] = []
    phase_wc: dict[str, list[float]] = {}

    for a in audits:
        wc = a.get("wallclock_s")
        if isinstance(wc, (int, float)) and wc == wc:  # not NaN
            by_var.setdefault(a.get("variation") or "?", []).append(float(wc))
            all_wc.append(float(wc))
        for ph, dt in (a.get("phase_deltas_s") or {}).items():
            if isinstance(dt, (int, float)):
                phase_wc.setdefault(ph, []).append(float(dt))

    def _stats(xs: list[float]) -> dict[str, float]:
        if not xs:
            return {"n": 0}
        xs_sorted = sorted(xs)

        def _pct(p: float) -> float:
            if not xs_sorted:
                return float("nan")
            k = max(0, min(len(xs_sorted) - 1, int(round(p * (len(xs_sorted) - 1)))))
            return xs_sorted[k]

        return {
            "n": len(xs),
            "mean": statistics.fmean(xs),
            "median": statistics.median(xs),
            "p95": _pct(0.95),
            "max": max(xs),
            "min": min(xs),
        }

    return {
        "overall_wallclock_stats": _stats(all_wc),
        "per_variation_wallclock_stats": {k: _stats(v) for k, v in by_var.items()},
        "per_phase_first_seen_offset_stats": {
            k: _stats(v) for k, v in phase_wc.items()
        },
    }


# ---------------------------------------------------------------------------
# LLM cost log check
# ---------------------------------------------------------------------------


def _scan_cost_log(start_offset_bytes: int) -> dict[str, Any]:
    """Check llm_cost_log.jsonl for any non-anthropic provider entries
    appended during this run."""
    path = ROOT / "outputs" / "llm_cost_log.jsonl"
    if not path.exists():
        return {
            "exists": False,
            "new_lines": 0,
            "providers_seen": [],
            "non_anthropic_count": 0,
        }
    providers: dict[str, int] = {}
    new_count = 0
    non_anthropic: list[dict[str, Any]] = []
    with path.open("rb") as f:
        f.seek(start_offset_bytes)
        for line in f:
            new_count += 1
            try:
                obj = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            prov = str(obj.get("provider") or "")
            providers[prov] = providers.get(prov, 0) + 1
            if prov and prov.lower() != "anthropic":
                non_anthropic.append(obj)
    return {
        "exists": True,
        "new_lines": new_count,
        "providers_seen": providers,
        "non_anthropic_count": len(non_anthropic),
        "non_anthropic_examples": non_anthropic[:5],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    install_mocks()
    sink = _AuditSink()
    sink.install()

    cost_log_path = ROOT / "outputs" / "llm_cost_log.jsonl"
    cost_log_start = cost_log_path.stat().st_size if cost_log_path.exists() else 0

    audits: list[dict[str, Any]] = []
    overall_t0 = time.monotonic()
    try:
        global_idx = 0
        for variation in VARIATIONS:
            n = int(variation["events"])
            if variation["rapid_fire"]:
                base = global_idx + 1
                print(
                    f"[L2] === RAPID-FIRE variation={variation['name']}"
                    f" {n} events ===",
                    flush=True,
                )
                v_audits = await _run_rapid_fire(base, variation, sink)
                for a in v_audits:
                    audits.append(a)
                    event_id = a.get("event_id", "?")
                    status = (a.get("summary") or {}).get("status", "?")
                    wc = a.get("wallclock_s", 0.0)
                    print(
                        f"[L2] event_id={event_id} status={status}"
                        f" pub_wallclock={wc:.2f}s",
                        flush=True,
                    )
                    if isinstance(event_id, int):
                        out_path = OUTPUT_DIR / f"audit_event_{event_id}.json"
                        out_path.write_text(
                            json.dumps(a, indent=2, default=str)
                        )
                global_idx += n
            else:
                for sub in range(n):
                    global_idx += 1
                    print(
                        f"[L2] === {global_idx}/{NUM_EVENTS} variation="
                        f"{variation['name']} sub={sub+1}/{n} ===",
                        flush=True,
                    )
                    audit = await _run_one(global_idx, variation, sub, sink)
                    audits.append(audit)
                    event_id = audit.get("event_id", "?")
                    status = (audit.get("summary") or {}).get("status", "?")
                    wc = audit.get("wallclock_s", 0.0)
                    print(
                        f"[L2] event_id={event_id} status={status}"
                        f" wallclock={wc:.2f}s",
                        flush=True,
                    )
                    if isinstance(event_id, int):
                        out_path = OUTPUT_DIR / f"audit_event_{event_id}.json"
                        out_path.write_text(
                            json.dumps(audit, indent=2, default=str)
                        )
    finally:
        sink.uninstall()
        uninstall_mocks()

    cost_log_check = _scan_cost_log(cost_log_start)
    timing_stats = _aggregate_timing(audits)

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
        "cost_log_check": cost_log_check,
        "timing_stats": timing_stats,
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
                "title_round_trip_ok": (
                    a.get("title_metrics", {}).get("title_round_trip_ok")
                ),
            }
            for a in audits
        ],
    }

    summary_path = OUTPUT_DIR / "L2_audit_summary.json"
    summary_path.write_text(json.dumps(overall, indent=2, default=str))
    print(f"[L2] wrote {summary_path}")

    timing_path = OUTPUT_DIR / "timing_stats.json"
    timing_path.write_text(json.dumps(timing_stats, indent=2, default=str))
    print(f"[L2] wrote {timing_path}")

    submitted = overall["submitted_count"]
    print(f"\n[L2] SUBMITTED {submitted}/{len(audits)}")
    print(f"[L2] mock_llm_calls={_mocks_mod.mock_llm_calls}")
    print(f"[L2] cost_log new lines={cost_log_check.get('new_lines')}")
    print(
        f"[L2] cost_log providers={cost_log_check.get('providers_seen')}"
    )


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
