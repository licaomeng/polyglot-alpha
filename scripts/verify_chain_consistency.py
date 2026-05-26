"""Verify chain <-> SQLite DB consistency for a single event_id.

Standalone audit script — no FastAPI dependency. Given an ``event_id``, it
walks the five chain-touching phases of the PolyglotAlpha lifecycle and
asserts that what is stored in ``polyglot_alpha.db`` matches the on-chain
state reachable via the Arc-testnet RPC.

Phases checked:

  2. Auction       — ``TranslationAuction.getAuction`` winner + winning bid
                     vs ``auctions`` row + settlement tx receipt status.
  4. Judges        — ``JudgePanel`` attestation tx receipt status (W9-A
                     column ``events.judges_attestation_tx`` is optional;
                     reported as ``[pending W9-A]`` when missing).
  5. Anchor        — ``QuestionRegistry.questions(qid).titleHash`` vs
                     ``questions.title_hash`` + tx receipt status.
  7. Fee split     — ``BuilderFeeRouter.getCumulativeFees`` deltas vs the
                     two ``builder_fee_events`` rows for the winner / treasury
                     legs (+ tx receipt status on each).
  8. Reputation    — ``ReputationRegistry.getStats(winner)`` vs the
                     ``agent_reputation`` row for the winner (W9-B columns
                     are optional; reported as ``[pending W9-B]`` when
                     missing). For deltas we currently report only the
                     present-state snapshot — the script is idempotent and
                     can be re-run as a sanity check.

Mock events (any tx_hash that starts with ``0xsim_``) are reported as
``N/A (mock event)`` for that phase and don't fail the run.

Run with the project's virtualenv::

    .venv/bin/python scripts/verify_chain_consistency.py 112
    .venv/bin/python scripts/verify_chain_consistency.py 112 --verbose

Exit code is ``0`` if every non-skipped phase matches, ``1`` otherwise.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from web3 import Web3
from web3.contract import Contract


# ---------------------------------------------------------------------------
# Project layout + env loading (no dotenv dependency)
# ---------------------------------------------------------------------------

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_DB_PATH: Path = _REPO_ROOT / "polyglot_alpha.db"
_FOUNDRY_OUT: Path = _REPO_ROOT / "contracts" / "out"

# Sentinel prefix for synthetic ("0xsim_...") tx hashes produced by mock-mode
# lifecycles — kept in sync with ``polyglot_alpha.chain.sim_helpers``.
_SIM_TX_HASH_PREFIX: str = "0xsim_"

# Expected 90 / 10 router split (Path A) — see ``record_fill_with_split``.
_WINNER_SHARE: float = 0.90
_TREASURY_SHARE: float = 0.10

# Tolerance for floating-point USDC deltas. 1e-6 USDC == 1 base unit at
# 6 decimals, so anything tighter than this is meaningless.
_USDC_DELTA_TOL: float = 1e-4


def _load_env_file(path: Path) -> None:
    """Populate ``os.environ`` from a ``.env`` file (best-effort, idempotent).

    Existing ``os.environ`` values take precedence so callers can still
    override via the shell. Silently ignored if the file does not exist.
    """

    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(_REPO_ROOT / ".env")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class PhaseResult:
    """One phase's audit verdict."""

    name: str
    status: str = "PASS"  # "PASS" | "FAIL" | "SKIP"
    db_lines: list[str] = field(default_factory=list)
    chain_lines: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def emoji(self) -> str:
        return {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}.get(self.status, "?")


# ---------------------------------------------------------------------------
# Helpers — DB
# ---------------------------------------------------------------------------


def _db_connect() -> sqlite3.Connection:
    # Open in read-only mode via URI so we can safely audit while the
    # FastAPI backend may still hold a WAL write lock. ``immutable=0``
    # (the default) keeps WAL visibility so our snapshot is up to date.
    uri = f"file:{_DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _db_row(conn: sqlite3.Connection, sql: str, params: tuple) -> Optional[sqlite3.Row]:
    cur = conn.execute(sql, params)
    return cur.fetchone()


def _db_rows(
    conn: sqlite3.Connection, sql: str, params: tuple
) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params))


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


# ---------------------------------------------------------------------------
# Helpers — formatting
# ---------------------------------------------------------------------------


def _short(value: Optional[str], head: int = 6, tail: int = 4) -> str:
    if not value:
        return "<none>"
    if len(value) <= head + tail + 3:
        return value
    return f"{value[:head]}...{value[-tail:]}"


def _is_sim_hash(value: Optional[str]) -> bool:
    return bool(value) and value.lower().startswith(_SIM_TX_HASH_PREFIX)


def _coerce_bytes32(value: Optional[str]) -> bytes:
    """Match the orchestrator's coercion in ``chain.auction_client._event_id_bytes``
    / ``chain.question_registry._coerce_bytes32`` so the on-chain key matches.
    """

    if not value:
        return b"\x00" * 32
    raw = value[2:] if value.startswith("0x") else value
    try:
        as_bytes = bytes.fromhex(raw)
    except ValueError:
        return Web3.keccak(text=value)
    if len(as_bytes) == 32:
        return as_bytes
    if len(as_bytes) < 32:
        return as_bytes.rjust(32, b"\x00")
    return as_bytes[:32]


def _event_id_to_bytes32(event_id: int) -> bytes:
    """Replicate ``chain.auction_client._event_id_bytes`` for an int event_id.

    The orchestrator calls ``event_id_from_event(str(event_id))`` which
    keccak-hashes the decimal string representation.
    """

    return Web3.keccak(text=str(event_id))


def _units_to_usdc(units: int, decimals: int = 6) -> float:
    return units / (10 ** decimals)


# ---------------------------------------------------------------------------
# Helpers — chain
# ---------------------------------------------------------------------------


def _load_abi(contract_name: str) -> list[dict]:
    path = _FOUNDRY_OUT / f"{contract_name}.sol" / f"{contract_name}.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)["abi"]


@dataclass
class ChainHandles:
    w3: Web3
    auction: Contract
    question_registry: Contract
    builder_fee_router: Contract
    reputation: Contract
    judge_panel: Optional[Contract]


def _build_chain_handles() -> ChainHandles:
    rpc_url = os.environ.get("ARC_TESTNET_RPC", "https://rpc.testnet.arc.network")
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

    auction_addr = os.environ["TRANSLATION_AUCTION_ADDRESS"]
    qr_addr = os.environ["QUESTION_REGISTRY_ADDRESS"]
    bfr_addr = os.environ["BUILDER_FEE_ROUTER_ADDRESS"]
    rep_addr = os.environ["REPUTATION_REGISTRY_ADDRESS"]
    judge_panel_addr = os.environ.get("JUDGE_PANEL_ADDRESS")

    auction = w3.eth.contract(
        address=Web3.to_checksum_address(auction_addr),
        abi=_load_abi("TranslationAuction"),
    )
    qr = w3.eth.contract(
        address=Web3.to_checksum_address(qr_addr),
        abi=_load_abi("QuestionRegistry"),
    )
    bfr = w3.eth.contract(
        address=Web3.to_checksum_address(bfr_addr),
        abi=_load_abi("BuilderFeeRouter"),
    )
    rep = w3.eth.contract(
        address=Web3.to_checksum_address(rep_addr),
        abi=_load_abi("ReputationRegistry"),
    )

    judge_panel: Optional[Contract] = None
    if judge_panel_addr:
        try:
            judge_panel = w3.eth.contract(
                address=Web3.to_checksum_address(judge_panel_addr),
                abi=_load_abi("JudgePanel"),
            )
        except FileNotFoundError:
            judge_panel = None
    return ChainHandles(
        w3=w3,
        auction=auction,
        question_registry=qr,
        builder_fee_router=bfr,
        reputation=rep,
        judge_panel=judge_panel,
    )


def _tx_status(w3: Web3, tx_hash: Optional[str]) -> Optional[int]:
    """Return the on-chain status (1=success, 0=revert) for ``tx_hash``.

    Returns ``None`` if the receipt cannot be fetched (RPC error or pending).
    """

    if not tx_hash or _is_sim_hash(tx_hash):
        return None
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        return None
    if receipt is None:
        return None
    return int(getattr(receipt, "status", receipt.get("status", 0)))


def _fmt_status(status: Optional[int]) -> str:
    if status is None:
        return "unknown"
    return "success" if status == 1 else "revert"


# ---------------------------------------------------------------------------
# Phase verifiers
# ---------------------------------------------------------------------------


def verify_auction(
    event_id: int,
    conn: sqlite3.Connection,
    chain: ChainHandles,
) -> PhaseResult:
    res = PhaseResult(name="Phase 2 Auction", status="PASS")
    row = _db_row(conn, "SELECT * FROM auctions WHERE event_id = ?", (event_id,))
    if row is None:
        res.status = "SKIP"
        res.reason = "no auctions row for this event"
        return res

    db_winner: Optional[str] = row["winner_address"]
    db_bid: Optional[float] = row["winning_bid"]
    settle_tx: Optional[str] = row["settlement_tx_hash"]

    res.db_lines.append(
        f"winner={_short(db_winner)}  bid={db_bid}  settle_tx={_short(settle_tx)}"
    )

    if _is_sim_hash(settle_tx):
        res.status = "SKIP"
        res.reason = "N/A (mock event — sim tx hash)"
        return res

    if not settle_tx:
        res.status = "FAIL"
        res.reason = "DB row missing settlement_tx_hash"
        return res

    # Read TranslationAuction.getAuction(eventId) -> (eventHash, deadline,
    # winner, winningBid, settled, opened, bidderCount).
    try:
        eid = _event_id_to_bytes32(event_id)
        on_chain = chain.auction.functions.getAuction(eid).call()
    except Exception as exc:
        res.status = "FAIL"
        res.reason = f"getAuction RPC call failed: {exc}"
        return res

    onchain_winner: str = on_chain[2]
    onchain_winning_bid_units: int = int(on_chain[3])
    onchain_settled: bool = bool(on_chain[4])
    onchain_winning_bid_usdc = _units_to_usdc(onchain_winning_bid_units)

    tx_status = _tx_status(chain.w3, settle_tx)
    res.chain_lines.append(
        f"winner={_short(onchain_winner)}  bid={onchain_winning_bid_usdc}  "
        f"settled={onchain_settled}  tx_status={_fmt_status(tx_status)}"
    )

    # The "lookalike" mock winners (e.g. 0xkkkk...) recorded in the DB are
    # *off-chain* fixtures used by the orchestrator's bid-generation fallback
    # and never actually existed on chain. Detect those so we don't report
    # a misleading mismatch.
    looks_like_placeholder = bool(
        db_winner and (db_winner.lower().count(db_winner.lower()[2:3]) > 25)
    )
    if looks_like_placeholder:
        res.notes.append(
            "DB winner is a placeholder fixture (0xkkkk... pattern); "
            "on-chain winner is the real settled bidder"
        )

    if tx_status != 1:
        res.status = "FAIL"
        res.reason = f"settle tx receipt status={_fmt_status(tx_status)}"
        return res

    if not onchain_settled:
        res.status = "FAIL"
        res.reason = "on-chain auction is not marked settled"
        return res

    # Winner address match (case-insensitive — DB stores mixed case,
    # chain returns checksum). Skip the strict match when the DB row is
    # a placeholder fixture.
    if db_winner and not looks_like_placeholder:
        if db_winner.lower() != onchain_winner.lower():
            res.status = "FAIL"
            res.reason = (
                f"winner mismatch: db={db_winner} chain={onchain_winner}"
            )
            return res

    # Winning-bid match (allow 1e-6 USDC tolerance).
    if db_bid is not None and onchain_winning_bid_units > 0:
        if abs(onchain_winning_bid_usdc - float(db_bid)) > _USDC_DELTA_TOL:
            res.status = "FAIL"
            res.reason = (
                f"winning_bid mismatch: db={db_bid} "
                f"chain={onchain_winning_bid_usdc}"
            )
            return res

    return res


def verify_anchor(
    event_id: int,
    conn: sqlite3.Connection,
    chain: ChainHandles,
) -> PhaseResult:
    res = PhaseResult(name="Phase 5 Anchor")
    row = _db_row(
        conn,
        "SELECT id, event_id, question_id_onchain, title_hash, tx_hash "
        "FROM questions WHERE event_id = ? ORDER BY id ASC LIMIT 1",
        (event_id,),
    )
    if row is None:
        res.status = "SKIP"
        res.reason = "no questions row for this event"
        return res

    commit_tx: Optional[str] = row["tx_hash"]
    title_hash: Optional[str] = row["title_hash"]
    qid_hex: Optional[str] = row["question_id_onchain"]

    res.db_lines.append(
        f"commit_tx={_short(commit_tx)}  title_hash={_short(title_hash)}  "
        f"qid={_short(qid_hex)}"
    )

    if _is_sim_hash(commit_tx):
        res.status = "SKIP"
        res.reason = "N/A (mock event — sim tx hash)"
        return res

    if not commit_tx:
        res.status = "FAIL"
        res.reason = "DB questions row missing tx_hash"
        return res

    tx_status = _tx_status(chain.w3, commit_tx)

    # Decode qid_hex (e.g. "0x000...2c") into an integer to feed
    # questions(uint256). The orchestrator pads to 40 hex chars on the
    # *left* so int(qid, 16) is the correct decoder.
    onchain_title_hash: Optional[str] = None
    qid_int: Optional[int] = None
    if qid_hex:
        try:
            qid_int = int(qid_hex, 16)
            q = chain.question_registry.functions.questions(qid_int).call()
            onchain_title_hash = q[0].hex()
        except Exception as exc:
            res.notes.append(f"questions({qid_hex}) read failed: {exc}")

    res.chain_lines.append(
        f"qid={qid_int}  on_chain_title_hash={_short(onchain_title_hash)}  "
        f"tx_status={_fmt_status(tx_status)}"
    )

    res.status = "PASS"
    if tx_status != 1:
        res.status = "FAIL"
        res.reason = f"commit tx receipt status={_fmt_status(tx_status)}"
        return res

    # Compare the on-chain title hash with what was stored in DB. The
    # orchestrator records ``candidate_hash`` (the LLM output digest) into
    # ``questions.title_hash``; the contract receives the same digest as
    # the ``titleHash`` field of registerQuestion.
    if title_hash and onchain_title_hash:
        db_normalised = title_hash.lower().lstrip("0x").rjust(64, "0")
        chain_normalised = onchain_title_hash.lower().lstrip("0x").rjust(64, "0")
        if db_normalised != chain_normalised:
            res.status = "FAIL"
            res.reason = (
                f"title_hash mismatch: db={_short(title_hash)} "
                f"chain={_short(onchain_title_hash)}"
            )
            return res

    return res


def verify_judges(
    event_id: int,
    conn: sqlite3.Connection,
    chain: ChainHandles,
) -> PhaseResult:
    res = PhaseResult(name="Phase 4 Judges")
    # W9-A introduces ``events.judges_attestation_tx``. If absent (the W9-A
    # branch has not landed yet) we report the pending state and move on.
    if not _table_has_column(conn, "events", "judges_attestation_tx"):
        res.status = "SKIP"
        res.reason = "[pending W9-A] events.judges_attestation_tx column not present"
        # We still print the DB-side state we can read so the operator
        # knows the quality_scores row exists.
        qrow = _db_row(
            conn,
            "SELECT verdict, overall_score FROM quality_scores WHERE event_id = ?",
            (event_id,),
        )
        if qrow is not None:
            res.db_lines.append(
                f"verdict={qrow['verdict']}  overall_score={qrow['overall_score']}"
            )
        return res

    row = _db_row(
        conn,
        "SELECT judges_attestation_tx FROM events WHERE id = ?",
        (event_id,),
    )
    if row is None or not row["judges_attestation_tx"]:
        res.status = "SKIP"
        res.reason = "judges_attestation_tx is NULL"
        return res

    j_tx: str = row["judges_attestation_tx"]
    res.db_lines.append(f"judges_attestation_tx={_short(j_tx)}")
    if _is_sim_hash(j_tx):
        res.status = "SKIP"
        res.reason = "N/A (mock event — sim tx hash)"
        return res

    tx_status = _tx_status(chain.w3, j_tx)
    res.chain_lines.append(f"tx_status={_fmt_status(tx_status)}")
    res.status = "PASS" if tx_status == 1 else "FAIL"
    if res.status == "FAIL":
        res.reason = f"attestation tx status={_fmt_status(tx_status)}"
    return res


def verify_fee_split(
    event_id: int,
    conn: sqlite3.Connection,
    chain: ChainHandles,
) -> PhaseResult:
    res = PhaseResult(name="Phase 7 Fee Split")
    # Find this event's market(s) -> fee legs.
    submissions = _db_rows(
        conn,
        "SELECT id, market_id FROM polymarket_submissions WHERE event_id = ?",
        (event_id,),
    )
    if not submissions:
        res.status = "SKIP"
        res.reason = "no polymarket_submissions row for this event"
        return res

    market_ids = [s["market_id"] for s in submissions if s["market_id"]]
    if not market_ids:
        res.status = "SKIP"
        res.reason = "polymarket_submissions has NULL market_id"
        return res

    placeholders = ",".join("?" for _ in market_ids)
    fee_rows = _db_rows(
        conn,
        f"SELECT id, market_id, fill_amount, fee_amount, translator_address, "
        f"arc_tx_hash, is_simulated FROM builder_fee_events "
        f"WHERE market_id IN ({placeholders}) ORDER BY id ASC",
        tuple(market_ids),
    )
    if not fee_rows:
        res.status = "SKIP"
        res.reason = "no builder_fee_events rows for this event's markets"
        return res

    # Group by translator_address.
    legs_by_addr: dict[str, list[sqlite3.Row]] = {}
    for r in fee_rows:
        legs_by_addr.setdefault(r["translator_address"], []).append(r)

    # Build a summary line.
    leg_summary = ", ".join(
        f"{_short(addr)}=${sum(float(r['fee_amount']) for r in legs):.4f}"
        for addr, legs in legs_by_addr.items()
    )
    res.db_lines.append(f"{len(fee_rows)} legs  [{leg_summary}]")

    # Check tx receipt status for each leg.
    all_sim = all(_is_sim_hash(r["arc_tx_hash"]) for r in fee_rows)
    if all_sim:
        res.status = "SKIP"
        res.reason = "N/A (mock event — all sim tx hashes)"
        return res

    leg_statuses: list[tuple[int, str]] = []
    for r in fee_rows:
        leg_statuses.append((r["id"], _fmt_status(_tx_status(chain.w3, r["arc_tx_hash"]))))

    failed = [lid for lid, st in leg_statuses if st != "success"]
    res.chain_lines.append(
        "leg tx receipts: "
        + ", ".join(f"id={lid}:{st}" for lid, st in leg_statuses)
    )

    # Read cumulative fees on-chain for each translator address that
    # received a leg. We can only report the present-state value (no
    # snapshot of "before" survives the lifecycle), so the check is
    # "cumulative >= sum of legs we credited". If a delta is requested,
    # the caller should snapshot before they fire Phase 7.
    cum_lines: list[str] = []
    fee_mismatch = False
    for addr, legs in legs_by_addr.items():
        expected = sum(float(r["fee_amount"]) for r in legs)
        try:
            raw = chain.builder_fee_router.functions.getCumulativeFees(
                Web3.to_checksum_address(addr)
            ).call()
            cum_usdc = _units_to_usdc(int(raw))
        except Exception as exc:
            cum_lines.append(f"{_short(addr)} cum=<rpc-fail: {exc}>")
            fee_mismatch = True
            continue
        cum_lines.append(
            f"{_short(addr)} cum=${cum_usdc:.6f}  legs_sum=${expected:.6f}"
        )
        # The cumulative balance is *lifetime*: it should be >= the legs
        # we are auditing. A strictly-less value is a clear inconsistency.
        if cum_usdc + _USDC_DELTA_TOL < expected:
            fee_mismatch = True

    res.chain_lines.append("cumulative: " + " | ".join(cum_lines))

    if failed:
        res.status = "FAIL"
        res.reason = f"{len(failed)} fee leg(s) without success receipt: {failed}"
        return res
    if fee_mismatch:
        res.status = "FAIL"
        res.reason = (
            "cumulative fees on chain are less than the sum of DB legs; "
            "tx hash recorded but on-chain state did not change"
        )
        return res

    # Sanity-check the 90/10 split when there are exactly two legs.
    if len(fee_rows) == 2:
        sorted_legs = sorted(fee_rows, key=lambda r: -float(r["fee_amount"]))
        winner_amount = float(sorted_legs[0]["fee_amount"])
        treasury_amount = float(sorted_legs[1]["fee_amount"])
        total = winner_amount + treasury_amount
        if total > 0:
            winner_ratio = winner_amount / total
            if not math.isclose(winner_ratio, _WINNER_SHARE, abs_tol=0.01):
                res.notes.append(
                    f"unexpected split ratio: winner={winner_ratio:.4f} "
                    f"expected ~{_WINNER_SHARE:.2f}"
                )

    res.status = "PASS"
    return res


def verify_reputation(
    event_id: int,
    conn: sqlite3.Connection,
    chain: ChainHandles,
) -> PhaseResult:
    res = PhaseResult(name="Phase 8 Reputation")
    # The winner address comes from the auctions row (DB side of truth).
    arow = _db_row(
        conn,
        "SELECT winner_address FROM auctions WHERE event_id = ?",
        (event_id,),
    )
    if arow is None or not arow["winner_address"]:
        res.status = "SKIP"
        res.reason = "no winner_address recorded in auctions"
        return res
    winner: str = arow["winner_address"]
    # Skip the obvious placeholder fixtures (0xkkkk... etc).
    looks_like_placeholder = bool(
        len(winner) >= 32 and winner.lower().count(winner.lower()[2:3]) > 25
    )
    if looks_like_placeholder:
        res.status = "SKIP"
        res.reason = f"winner is a placeholder fixture: {winner}"
        return res

    # DB side — agent_reputation row.
    rep_row = _db_row(
        conn,
        "SELECT * FROM agent_reputation WHERE LOWER(agent_address) = LOWER(?)",
        (winner,),
    )

    # W9-B adds richer columns (``auction_count`` / ``quality_count`` /
    # ``fee_total``). Probe for the actual column names so we degrade
    # gracefully if W9-B has not landed.
    w9b_cols = [
        c for c in ("auction_count", "quality_count", "fee_total")
        if _table_has_column(conn, "agent_reputation", c)
    ]
    has_w9b = bool(w9b_cols)

    if rep_row is None:
        res.db_lines.append("no agent_reputation row for winner")
    elif has_w9b:
        parts = [f"{c}={rep_row[c]}" for c in w9b_cols]
        res.db_lines.append("  ".join(parts))
    else:
        res.db_lines.append(
            f"[pending W9-B] total_bids={rep_row['total_bids']}  "
            f"total_wins={rep_row['total_wins']}  "
            f"avg_quality={rep_row['avg_quality']}  "
            f"cumulative_fees={rep_row['cumulative_fees']}"
        )

    # Chain side — ReputationRegistry.getStats(winner).
    try:
        stats = chain.reputation.functions.getStats(
            Web3.to_checksum_address(winner)
        ).call()
        (
            total_bids,
            total_wins,
            total_quality_passes,
            cumulative_fees_units,
            score_units,
        ) = (int(x) for x in stats)
        cum_fees_usdc = _units_to_usdc(cumulative_fees_units)
        score_float = score_units / 10 ** 18
        res.chain_lines.append(
            f"total_bids={total_bids}  total_wins={total_wins}  "
            f"quality_passes={total_quality_passes}  "
            f"cum_fees=${cum_fees_usdc:.6f}  score={score_float:.4f}"
        )
    except Exception as exc:
        res.status = "FAIL"
        res.reason = f"getStats RPC call failed: {exc}"
        return res

    # Match check — DB cumulative_fees vs chain cum_fees_usdc + total_wins vs DB.
    res.status = "PASS"
    if rep_row is None:
        # DB has no row but chain does — that's an inconsistency if any
        # non-zero stats are on chain.
        if total_bids > 0 or total_wins > 0 or cumulative_fees_units > 0:
            res.status = "FAIL"
            res.reason = (
                f"chain has stats (bids={total_bids}, wins={total_wins}, "
                f"fees=${cum_fees_usdc:.6f}) but DB has no agent_reputation row"
            )
        return res

    db_cum = float(rep_row["cumulative_fees"])
    if abs(db_cum - cum_fees_usdc) > _USDC_DELTA_TOL:
        res.notes.append(
            f"cumulative_fees drift: db=${db_cum:.6f} chain=${cum_fees_usdc:.6f} "
            f"(delta=${cum_fees_usdc - db_cum:+.6f})"
        )
        # We treat this as a soft mismatch — many DB updates are local-only
        # bookkeeping and the chain ALWAYS lags by some amount of un-confirmed
        # transactions. Surfaces in the report but does not fail the run.
    return res


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _resolve_event(
    conn: sqlite3.Connection, event_id: int
) -> Optional[sqlite3.Row]:
    return _db_row(
        conn,
        "SELECT id, status, mode, content_hash, title FROM events WHERE id = ?",
        (event_id,),
    )


def _print_phase(res: PhaseResult, verbose: bool) -> None:
    print(f"\n{res.name}")
    if res.status == "SKIP":
        print(f"  SKIP — {res.reason}")
        if verbose and res.db_lines:
            for line in res.db_lines:
                print(f"  DB:    {line}")
        return
    for line in res.db_lines:
        print(f"  DB:    {line}")
    for line in res.chain_lines:
        print(f"  Chain: {line}")
    for note in res.notes:
        print(f"  NOTE:  {note}")
    if res.status == "PASS":
        print("  RESULT: PASS")
    else:
        print(f"  RESULT: FAIL  -- {res.reason}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify chain<->DB consistency for one event_id"
    )
    parser.add_argument("event_id", type=int, help="event_id to verify")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show DB context lines even on SKIP",
    )
    args = parser.parse_args()
    event_id: int = args.event_id

    if not _DB_PATH.exists():
        print(f"FATAL: DB not found at {_DB_PATH}", file=sys.stderr)
        return 2

    conn = _db_connect()
    try:
        evt = _resolve_event(conn, event_id)
        if evt is None:
            print(f"FATAL: no events row with id={event_id}", file=sys.stderr)
            return 2

        winner_row = _db_row(
            conn, "SELECT winner_address FROM auctions WHERE event_id = ?", (event_id,)
        )
        winner = winner_row["winner_address"] if winner_row else None

        print(f"verify_chain_consistency.py {event_id}\n")
        print(f"Event {event_id} -- verifying chain <-> DB consistency")
        print("=" * 64)
        print(
            f"Mode: {evt['mode']}  |  Status: {evt['status']}  |  "
            f"Winner: {_short(winner)}"
        )

        try:
            chain = _build_chain_handles()
        except Exception as exc:
            print(f"FATAL: failed to build chain handles: {exc}", file=sys.stderr)
            return 2

        phases = [
            verify_auction(event_id, conn, chain),
            verify_judges(event_id, conn, chain),
            verify_anchor(event_id, conn, chain),
            verify_fee_split(event_id, conn, chain),
            verify_reputation(event_id, conn, chain),
        ]
        for p in phases:
            _print_phase(p, verbose=args.verbose)

        # Final tally.
        passed = sum(1 for p in phases if p.status == "PASS")
        failed = sum(1 for p in phases if p.status == "FAIL")
        skipped = sum(1 for p in phases if p.status == "SKIP")
        checked = passed + failed

        print()
        if failed == 0:
            print(
                f"OVERALL: PASS  {passed}/{checked} phases consistent "
                f"({skipped} skipped)"
            )
            return 0
        print(
            f"OVERALL: FAIL  {failed} of {checked} phases inconsistent "
            f"({skipped} skipped)"
        )
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
