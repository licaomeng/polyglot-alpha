"""Phase 1 smoke test — verifies the "no more mock" invariants.

Run this AFTER backend Phase 1 ship completes:
* ``polyglot_alpha/chain/`` package wired into the auction path,
* ``polyglot_alpha/agents/dispatch.py`` dispatching 4 real LLM agents,
* orchestrator + ``/trigger/event`` accepting ``event_source='rss'`` and
  emitting real BLEU/COMET/MQM and real or dry-run Polymarket submissions.

Each individual check is best-effort: a failure logs and the rest still
run, then the script writes ``outputs/smoke_test_phase1_result.json``
with a structured summary so CI / downstream agents can post-process.

Exit codes:

* ``0`` — every check passed.
* ``1`` — backend unreachable or some checks failed (see JSON for detail).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx


BACKEND = "http://localhost:8000"
_REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = _REPO_ROOT / "polyglot_alpha.db"
RESULT_PATH = _REPO_ROOT / "outputs" / "smoke_test_phase1_result.json"

# Trigger that exercises the new ``event_source='rss'`` path with a short
# auction window so the smoke test stays under ~2 min end-to-end.
TRIGGER_TIMEOUT_S = 180.0
TRIGGER_PAYLOAD: dict[str, Any] = {
    "event_source": "rss",
    "rss_window_minutes": 360,
    "auction_window_seconds": 0.0,
}


CHECKS: list[dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}: {detail}")


def _safe_json_loads(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


async def _backend_health(client: httpx.AsyncClient) -> bool:
    try:
        r = await client.get(f"{BACKEND}/health")
    except httpx.HTTPError as exc:
        _record("backend_health", False, f"unreachable: {exc!s}")
        return False
    ok = r.status_code == 200
    _record("backend_health", ok, f"HTTP {r.status_code}")
    return ok


async def _trigger_rss(
    client: httpx.AsyncClient,
) -> tuple[bool, dict[str, Any] | None]:
    """POST ``/trigger/event`` with ``event_source='rss'``.

    The old API rejected this body with HTTP 422; Phase 1 must accept it.
    Returns ``(ok, response_body_or_None)``.
    """

    try:
        r = await client.post(
            f"{BACKEND}/trigger/event",
            json=TRIGGER_PAYLOAD,
            timeout=TRIGGER_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        _record(
            "trigger_event_source_rss_no_422",
            False,
            f"request error: {exc!s}",
        )
        return False, None

    # 409 means dedup hit — still a successful schema-validated request.
    accepted = r.status_code in (200, 409)
    _record(
        "trigger_event_source_rss_no_422",
        accepted,
        f"HTTP {r.status_code}: {r.text[:200]}",
    )
    if not accepted:
        return False, None
    try:
        return True, r.json()
    except ValueError:
        return False, None


def _check_response_shape(result: dict[str, Any]) -> int | None:
    """Validate the high-level response payload returned by /trigger/event."""

    verdict = result.get("verdict")
    _record(
        "verdict_present",
        verdict in ("PASS", "FAIL", "BORDERLINE"),
        f"verdict={verdict}",
    )

    market_id = result.get("market_id") or ""
    real_or_dryrun = bool(market_id) and not market_id.startswith("mock-")
    _record(
        "market_id_real_or_dryrun",
        real_or_dryrun,
        f"market_id={market_id!r}",
    )

    tx_hash = (
        result.get("settlement_tx_hash")
        or result.get("commit_tx_hash")
        or result.get("tx_hash")
    )
    bad_tx = (not tx_hash) or tx_hash == "0x" + "0" * 64
    _record(
        "tx_hash_not_sha256_fake",
        not bad_tx,
        f"tx_hash={tx_hash!r}",
    )

    event_id = result.get("event_id")
    if isinstance(event_id, int):
        return event_id
    try:
        return int(event_id) if event_id is not None else None
    except (TypeError, ValueError):
        return None


def _check_quality_scores(con: sqlite3.Connection, event_id: int | None) -> None:
    if event_id is not None:
        row = con.execute(
            "SELECT translation_scores FROM quality_scores WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT translation_scores FROM quality_scores "
            "ORDER BY event_id DESC LIMIT 1"
        ).fetchone()

    if not row:
        _record("quality_scores_bleu_real", False, "no quality_scores row")
        _record("quality_scores_comet_real", False, "no quality_scores row")
        _record("quality_scores_mqm_real", False, "no quality_scores row")
        return

    scores = _safe_json_loads(row[0]) or {}
    bleu = scores.get("bleu")
    comet = scores.get("comet")
    mqm_blob = scores.get("mqm")
    if isinstance(mqm_blob, dict):
        mqm = mqm_blob.get("score")
    else:
        mqm = mqm_blob

    _record(
        "quality_scores_bleu_real",
        bleu is not None,
        f"BLEU={bleu!r}",
    )
    _record(
        "quality_scores_comet_real",
        comet is not None,
        f"COMET={comet!r}",
    )
    _record(
        "quality_scores_mqm_real",
        mqm is not None,
        f"MQM={mqm!r}",
    )


def _check_bids(con: sqlite3.Connection, event_id: int | None) -> None:
    if event_id is None:
        # Fall back to the most recent event in the events table.
        row = con.execute("SELECT MAX(id) FROM events").fetchone()
        event_id = row[0] if row else None

    if event_id is None:
        _record("four_agents_bid", False, "no events in DB")
        _record("bids_diverse", False, "no events in DB")
        return

    bids = con.execute(
        "SELECT agent_address, bid_amount FROM bids "
        "WHERE event_id = ? ORDER BY bid_amount",
        (event_id,),
    ).fetchall()
    agents = {b[0] for b in bids}
    amounts = [b[1] for b in bids]

    _record(
        "four_agents_bid",
        len(agents) == 4,
        f"event_id={event_id} unique_agents={len(agents)} amounts={amounts}",
    )
    _record(
        "bids_diverse",
        len(set(amounts)) > 1,
        f"unique amounts={len(set(amounts))}",
    )


def _check_polymarket(con: sqlite3.Connection, event_id: int | None) -> None:
    if event_id is not None:
        row = con.execute(
            "SELECT market_id, is_simulated FROM polymarket_submissions "
            "WHERE event_id = ? ORDER BY id DESC LIMIT 1",
            (event_id,),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT market_id, is_simulated FROM polymarket_submissions "
            "ORDER BY event_id DESC LIMIT 1"
        ).fetchone()

    if not row:
        _record(
            "polymarket_dryrun_mode",
            False,
            "no polymarket_submissions row",
        )
        return

    market_id, _is_sim = row[0] or "", row[1]
    # Phase 1 mode flags any non-mock submission as legitimate. The
    # ``dryrun-`` and ``real-`` prefixes are the two acceptable modes.
    is_phase1 = market_id.startswith(("dryrun-", "real-"))
    _record(
        "polymarket_dryrun_mode",
        is_phase1,
        f"market_id={market_id!r}",
    )


async def _check_submit_real(client: httpx.AsyncClient, event_id: int | None) -> None:
    target_id = event_id if event_id is not None else 1
    try:
        r = await client.post(
            f"{BACKEND}/events/{target_id}/polymarket/submit-real",
            json={},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        _record(
            "submit_real_endpoint_exists",
            False,
            f"request error: {exc!s}",
        )
        return
    # 4xx without ``confirm_real_polymarket`` is the expected handshake.
    ok = r.status_code in (400, 401, 403, 422)
    _record(
        "submit_real_endpoint_exists",
        ok,
        f"HTTP {r.status_code}: {r.text[:200]}",
    )


async def main() -> int:
    backend_ok = False
    event_id: int | None = None
    response: dict[str, Any] | None = None

    async with httpx.AsyncClient(timeout=TRIGGER_TIMEOUT_S) as client:
        backend_ok = await _backend_health(client)
        if backend_ok:
            triggered, response = await _trigger_rss(client)
            if triggered and isinstance(response, dict):
                event_id = _check_response_shape(response)

    if backend_ok:
        try:
            con = sqlite3.connect(str(DB_PATH))
            _check_quality_scores(con, event_id)
            _check_bids(con, event_id)
            _check_polymarket(con, event_id)
            con.close()
        except sqlite3.Error as exc:
            _record("db_inspect", False, f"sqlite error: {exc!s}")
    else:
        print(
            "[smoke] backend unreachable — skipping DB and submit-real checks"
        )

    if backend_ok:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await _check_submit_real(client, event_id)

    passed = sum(1 for c in CHECKS if c["ok"])
    total = len(CHECKS)
    print()
    print("=" * 60)
    print(f"Smoke test: {passed}/{total} checks passed")
    print("=" * 60)

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(
            {
                "backend_ok": backend_ok,
                "event_id": event_id,
                "passed": passed,
                "total": total,
                "checks": CHECKS,
                "trigger_response": response,
            },
            indent=2,
            default=str,
        )
    )
    print(f"[smoke] wrote {RESULT_PATH}")

    return 0 if (backend_ok and passed == total) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
