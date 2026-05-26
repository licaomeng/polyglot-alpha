"""End-to-end PASS-path audit harness.

Drives 5 events through the orchestrator's full happy path
(PENDING -> AUCTION_OPEN -> AUCTION_SETTLED -> TRANSLATING ->
EVALUATING -> COMMITTED -> SUBMITTED) WITHOUT spending real money on
Anthropic LLM calls. Uses :mod:`tests._pass_path_mocks` to:

* Patch :func:`polyglot_alpha.judges.panel.evaluate` -> canned PASS verdict.
* Patch :class:`polyglot_alpha.llm.AnthropicLLM` -> no-network stand-in.
* Patch :func:`polyglot_alpha.llm.make_llm` / :func:`complete` / :func:`complete_json`.
* Refuse to construct a real ``AsyncAnthropic`` client.

On-chain TXs (open auction, settle, commit question, recordFill 90/10
split) still execute against the Arc testnet using the configured
operator wallet — gas is free testnet ETH.

Polymarket submission is forced to ``POLYMARKET_MODE=dry_run`` so no
real market is created.

The script runs in the SAME process as :func:`run_lifecycle`, writing
to the shared ``polyglot_alpha.db`` SQLite file the FastAPI backend
reads. The backend therefore observes the new rows via its normal
``GET /events/{id}`` etc. (SSE is per-process so the backend's hub
won't see this run's events — we capture our own pubsub events
locally and stash them into the audit JSON).

Outputs: ``outputs/audit_event_{event_id}.json`` per event +
``outputs/audit_summary.json``.
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

# Make sure the package root is importable when invoked directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import _pass_path_mocks as _mocks_mod  # noqa: E402
from tests._pass_path_mocks import install_mocks, uninstall_mocks  # noqa: E402

# Tone down the orchestrator's chatty INFO logging so the audit runner's
# own output stays readable. Errors / warnings still surface.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Audit constants
# ---------------------------------------------------------------------------

NUM_EVENTS: int = 5
OUTPUT_DIR: Path = ROOT / "outputs"
PER_EVENT_TIMEOUT_S: float = 180.0

#: Headline reused for every audit event. The orchestrator wraps it into a
#: P1-shape question internally (``"Will X by December 31, YYYY?"``). We use
#: ``user_payload``-style headlines so the RSS / Haiku ingestion path is
#: skipped entirely (mission constraint: bypass RSS+Haiku to avoid LLM cost).
BASE_TITLE: str = (
    "Will the FOMC raise rates by 25bp at the June 2026 meeting?"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short(value: Any, length: int = 80) -> str:
    if value is None:
        return ""
    s = str(value)
    return s if len(s) <= length else s[: length - 1] + "..."


def _winner_address() -> str:
    """Pick the operator wallet address as the simulated translator winner.

    Using a checksummed 0x... address makes the orchestrator's "real-looking
    address" gate fire so the 90/10 split actually attempts the on-chain
    ``recordFill_with_split`` (vs the simulated-only fallback path).
    """

    addr = os.environ.get("HACKATHON_WALLET_ADDRESS")
    if addr and addr.startswith("0x") and len(addr) == 42:
        return addr
    # Deterministic stand-in if no operator wallet is configured.
    return "0xdeadbeef00000000000000000000000000000001"


def _treasury_address() -> str:
    return (
        os.environ.get("PLATFORM_TREASURY_ADDRESS")
        or os.environ.get("HACKATHON_WALLET_ADDRESS")
        or "0x000000000000000000000000000000000000dead"
    )


# ---------------------------------------------------------------------------
# DB capture
# ---------------------------------------------------------------------------


def _dump_event_rows(event_id: int) -> dict[str, Any]:
    """Snapshot every persisted row tied to ``event_id``.

    Uses raw sqlite3 (read-only) so the dump never collides with the
    backend's writes. JSON columns are parsed into nested dicts/lists.
    """

    import sqlite3

    db_path = ROOT / "polyglot_alpha.db"
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    def _query(sql: str, *args: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in con.execute(sql, args).fetchall():
            d = dict(row)
            # Best-effort JSON-decode the json-string columns.
            for k, v in list(d.items()):
                if (
                    isinstance(v, str)
                    and v
                    and v[0] in "[{"
                    and v[-1] in "]}"
                ):
                    try:
                        d[k] = json.loads(v)
                    except json.JSONDecodeError:
                        pass
            out.append(d)
        return out

    snapshot: dict[str, Any] = {
        "events": _query("SELECT * FROM events WHERE id=?", event_id),
        "bids": _query(
            "SELECT * FROM bids WHERE event_id=? ORDER BY id", event_id
        ),
        "auctions": _query(
            "SELECT * FROM auctions WHERE event_id=?", event_id
        ),
        "translations": _query(
            "SELECT * FROM translations WHERE event_id=?", event_id
        ),
        "quality_scores": _query(
            "SELECT * FROM quality_scores WHERE event_id=?", event_id
        ),
        "questions": _query(
            "SELECT * FROM questions WHERE event_id=?", event_id
        ),
        "polymarket_submissions": _query(
            "SELECT * FROM polymarket_submissions WHERE event_id=?", event_id
        ),
    }

    # Builder-fee events are keyed by market_id, not event_id. Look up the
    # market_id from the polymarket_submissions row first.
    market_ids = [
        s.get("market_id")
        for s in snapshot["polymarket_submissions"]
        if s.get("market_id")
    ]
    fee_rows: list[dict[str, Any]] = []
    for mid in market_ids:
        fee_rows.extend(
            _query(
                "SELECT * FROM builder_fee_events WHERE market_id=? ORDER BY id",
                mid,
            )
        )
    snapshot["builder_fee_events"] = fee_rows

    # Agent reputation rows for the winner (if any).
    rep_rows: list[dict[str, Any]] = []
    for r in snapshot["bids"]:
        addr = r.get("agent_address")
        if addr:
            rep_rows.extend(
                _query(
                    "SELECT * FROM agent_reputation WHERE agent_address=?",
                    addr,
                )
            )
    snapshot["agent_reputation"] = rep_rows

    con.close()
    return snapshot


# ---------------------------------------------------------------------------
# Pubsub capture
# ---------------------------------------------------------------------------


class _AuditSink:
    """Captures every publish() emitted by the orchestrator's pubsub hub.

    Wires in by patching :meth:`polyglot_alpha.pubsub.PubSub.publish` to
    fan out to both the original implementation AND our local buffer.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._installed = False
        self._orig: Any = None

    def install(self) -> None:
        if self._installed:
            return
        from polyglot_alpha import pubsub as pubsub_mod

        self._orig = pubsub_mod.PubSub.publish

        async def _wrapped(
            self_hub: Any, topic: str, payload: dict[str, Any]
        ) -> None:
            self.events.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "topic": topic,
                    "payload": payload,
                }
            )
            await self._orig(self_hub, topic, payload)

        pubsub_mod.PubSub.publish = _wrapped  # type: ignore[assignment]
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        from polyglot_alpha import pubsub as pubsub_mod

        pubsub_mod.PubSub.publish = self._orig  # type: ignore[assignment]
        self._installed = False

    def for_event(self, event_id: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ev in self.events:
            payload = ev.get("payload") or {}
            if payload.get("event_id") == event_id:
                out.append(ev)
        return out

    def reset(self) -> None:
        self.events.clear()


# ---------------------------------------------------------------------------
# Per-event runner
# ---------------------------------------------------------------------------


async def _run_one(idx: int, sink: _AuditSink) -> dict[str, Any]:
    """Drive one PASS-path lifecycle and return the audit JSON for it."""

    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle

    salt = uuid.uuid4().hex[:8]
    title = f"{BASE_TITLE} [audit-{idx}-{salt}]"
    event_dict: dict[str, Any] = {
        "title": title,
        "sources": [
            {
                "name": "audit-source",
                "url": f"https://audit.example/event/{salt}",
                "language": "en",
            }
        ],
        "language": "en",
        "category": "macro",
        "summary": (
            "The June 17-18, 2026 FOMC meeting decides rate policy. "
            "Audit synthetic source."
        ),
    }

    winner = _winner_address()
    runner_up = "0x" + "a" * 40
    second = "0x" + "b" * 40

    mock_bids = [
        BidRecord(
            agent_address=winner,
            bid_amount=0.45,
            stake_amount=5.0,
            candidate_hash=None,
            tx_hash=None,
            reputation=0.95,
        ),
        BidRecord(
            agent_address=runner_up,
            bid_amount=0.55,
            stake_amount=5.0,
            candidate_hash=None,
            tx_hash=None,
            reputation=0.9,
        ),
        BidRecord(
            agent_address=second,
            bid_amount=0.75,
            stake_amount=5.0,
            candidate_hash=None,
            tx_hash=None,
            reputation=0.85,
        ),
    ]

    sink.reset()
    phase_log: list[dict[str, Any]] = []
    t0 = time.monotonic()

    summary: dict[str, Any] = {}
    try:
        # Auction mode 'mock' deterministically produces a PASS-shaped
        # final_question via _run_translator_pipeline's fallback, while
        # the panel.evaluate patch returns a canned PASS verdict so the
        # commit + Polymarket + builder-fee path runs.
        summary = await asyncio.wait_for(
            run_lifecycle(
                event_dict,
                auction_window_seconds=0.0,
                mock_bids=mock_bids,
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
        # Couldn't locate the event row; emit a minimal failure dump.
        return {
            "audit_index": idx,
            "title": title,
            "wallclock_s": wallclock,
            "summary": summary,
            "error": "event_id missing from run_lifecycle result",
        }

    # Capture all DB rows belonging to this event.
    rows = _dump_event_rows(int(event_id))

    # Pull pubsub events for this event_id.
    pub_events = sink.for_event(int(event_id))

    # Compute per-phase wall-clock from pub events.
    phases_seen: dict[str, str] = {}
    for ev in pub_events:
        phases_seen.setdefault(ev["topic"], ev["timestamp"])

    # Subsystem boundary checks.
    polymarket_row = rows["polymarket_submissions"][0] if rows[
        "polymarket_submissions"
    ] else None
    question_row = rows["questions"][0] if rows["questions"] else None
    fee_rows = rows["builder_fee_events"]
    quality_row = rows["quality_scores"][0] if rows["quality_scores"] else None
    auction_row = rows["auctions"][0] if rows["auctions"] else None
    event_row = rows["events"][0] if rows["events"] else None

    # The 90/10 split: two fee rows, fee_amount totals to 1.0.
    fee_amounts = sorted(float(f.get("fee_amount") or 0.0) for f in fee_rows)
    fee_total = sum(fee_amounts) if fee_amounts else 0.0
    has_split_90 = any(abs(a - 0.9) < 1e-6 for a in fee_amounts)
    has_split_10 = any(abs(a - 0.1) < 1e-6 for a in fee_amounts)

    subsystem_status = {
        "rss_bypassed": True,  # user_payload-style title; no RSS poll triggered
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
            (r.get("agent_address") == winner)
            and (
                (r.get("total_wins") or 0) >= 1
                or float(r.get("cumulative_fees") or 0) > 0
            )
            for r in rows["agent_reputation"]
        ),
    }

    # Arc TX hashes captured.
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
        "event_id": event_id,
        "title": title,
        "wallclock_s": wallclock,
        "summary": summary,
        "phase_timestamps": phases_seen,
        "subsystem_status": subsystem_status,
        "tx_hashes": tx_hashes,
        "db_rows": rows,
        "pub_events": pub_events,
        "correlation_id": f"audit-{idx}-{salt}",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    install_mocks()
    sink = _AuditSink()
    sink.install()

    audits: list[dict[str, Any]] = []
    overall_t0 = time.monotonic()
    try:
        for i in range(1, NUM_EVENTS + 1):
            print(f"[audit] === Event {i}/{NUM_EVENTS} ===")
            audit = await _run_one(i, sink)
            audits.append(audit)
            event_id = audit.get("event_id", "?")
            status = (audit.get("summary") or {}).get("status", "?")
            wc = audit.get("wallclock_s", 0.0)
            print(
                f"[audit] event {event_id} -> {status} in {wc:.1f}s",
                flush=True,
            )
            # Persist per-event audit JSON immediately so a crash mid-run
            # doesn't lose earlier data.
            if isinstance(event_id, int):
                out_path = OUTPUT_DIR / f"audit_event_{event_id}.json"
                out_path.write_text(json.dumps(audit, indent=2, default=str))
                print(f"[audit] wrote {out_path}")
    finally:
        sink.uninstall()
        uninstall_mocks()

    overall = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "wallclock_total_s": time.monotonic() - overall_t0,
        "event_count": len(audits),
        "panel_evaluate_calls": _mocks_mod.panel_evaluate_calls,
        "mock_llm_calls": _mocks_mod.mock_llm_calls,
        "mock_llm_first_log": list(_mocks_mod.mock_llm_log[:20]),
        "audit_files": [
            str(OUTPUT_DIR / f"audit_event_{a.get('event_id')}.json")
            for a in audits
            if a.get("event_id") is not None
        ],
        "per_event": [
            {
                "audit_index": a["audit_index"],
                "event_id": a.get("event_id"),
                "status": (a.get("summary") or {}).get("status"),
                "subsystem_status": a.get("subsystem_status"),
                "wallclock_s": a.get("wallclock_s"),
            }
            for a in audits
        ],
    }

    summary_path = OUTPUT_DIR / "audit_summary.json"
    summary_path.write_text(json.dumps(overall, indent=2, default=str))
    print(f"[audit] wrote {summary_path}")

    # Stdout summary table
    print("\n[audit] ===== final =====")
    print(f"[audit] panel.evaluate calls (mocked): {_mocks_mod.panel_evaluate_calls}")
    print(f"[audit] mock_llm calls           : {_mocks_mod.mock_llm_calls}")
    submitted = sum(
        1
        for a in audits
        if (a.get("summary") or {}).get("status") == "SUBMITTED"
    )
    print(f"[audit] SUBMITTED                : {submitted}/{len(audits)}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
