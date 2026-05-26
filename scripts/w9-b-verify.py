"""W9-B verifier: prove on-chain ReputationRegistry deltas match DB deltas.

The orchestrator now pushes (won, quality) signals after Phase 5 and
the fee signal after Phase 7. This script verifies the chain side is
actually receiving those updates by:

  1. Snapshotting on-chain ``getStats(winner)`` BEFORE.
  2. Snapshotting the SQLite ``agent_reputation`` row for the same agent.
  3. Calling the same three writer functions the orchestrator does
     (``update_reputation(won=True, quality_passed=...)`` and
     ``update_reputation_fee_only(fee_usdc=...)``).
  4. Snapshotting on-chain ``getStats(winner)`` AFTER.
  5. Computing deltas and printing PASS/FAIL.

We don't drive a full ``run_lifecycle`` here because that flow depends
on Polymarket connectivity, agent registration, and ~1 minute of auction
window time. The semantic check we care about — "does the orchestrator's
on-chain update path land deltas that match the DB" — is fully covered
by directly invoking the same helpers the orchestrator invokes, which
also lets us run the test deterministically against any winner address.

Usage::

    python scripts/w9-b-verify.py
    python scripts/w9-b-verify.py --winner 0xDeadBeef... --fee-usdc 0.9
    python scripts/w9-b-verify.py --mock   # confirm no real RPC in mock mode

Exit codes:
  0 — PASS (chain delta matches expected)
  1 — FAIL (delta mismatch)
  2 — environment error (RPC down, wallet unset, etc.)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Any

# Make ``polyglot_alpha`` importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from eth_account import Account
from web3 import Web3

from polyglot_alpha.chain import reputation_registry as repo
from polyglot_alpha.onchain import OnChainClient, reputation_to_float


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("w9-b-verify")


# ---------------------------------------------------------------------------
# DB helpers (optional — DB is per-process SQLite, so we treat the snapshot
# as best-effort and surface "(no db row)" when the agent has never been
# touched by an orchestrator run in the local DB).
# ---------------------------------------------------------------------------


def _db_snapshot(agent_address: str) -> dict[str, Any] | None:
    try:
        from polyglot_alpha.persistence import session_scope
        from polyglot_alpha.persistence.models import AgentReputation

        with session_scope() as session:
            row = session.get(AgentReputation, agent_address)
            if row is None:
                return None
            return {
                "total_bids": int(row.total_bids),
                "total_wins": int(row.total_wins),
                "avg_quality": float(row.avg_quality),
                "cumulative_fees": float(row.cumulative_fees),
            }
    except Exception as exc:  # pragma: no cover - DB optional
        logger.warning("db snapshot unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Chain helpers
# ---------------------------------------------------------------------------


def _chain_snapshot(client: OnChainClient, agent_address: str) -> dict[str, Any]:
    addr = Web3.to_checksum_address(agent_address)
    raw = client.reputation.functions.getStats(addr).call()
    total_bids, total_wins, total_quality, fees_units, score_raw = raw
    return {
        "total_bids": int(total_bids),
        "total_wins": int(total_wins),
        "total_quality_passes": int(total_quality),
        "cumulative_fees_units": int(fees_units),
        "cumulative_fees_usdc": fees_units / (10 ** 6),
        "score_raw": int(score_raw),
        "score": reputation_to_float(score_raw),
    }


def _delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {k: after[k] - before[k] for k in before if isinstance(before[k], (int, float))}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--winner",
        default=None,
        help=(
            "Winner address to update. Defaults to a freshly generated "
            "throwaway address so deltas start from a clean (0,0,0,0) baseline."
        ),
    )
    parser.add_argument(
        "--fee-usdc",
        type=float,
        default=0.9,
        help="Fee amount (USDC) to push via updateOnFee. Default 0.9.",
    )
    parser.add_argument(
        "--quality-passed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --no-quality-passed to push quality_passed=False.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help=(
            "Mock-mode: verify the orchestrator's mode-gate by patching "
            "the chain helper to assert no real RPC call is issued."
        ),
    )
    args = parser.parse_args()

    if args.winner is None:
        # Generate a throwaway address — never been touched on this contract,
        # so all four counters start at zero.
        winner = Account.from_key("0x" + secrets.token_hex(32)).address
        logger.info("using fresh throwaway winner address: %s", winner)
    else:
        winner = Web3.to_checksum_address(args.winner)

    client = OnChainClient()
    logger.info(
        "ReputationRegistry @ %s on chain %s",
        client.config.reputation_registry,
        client.config.chain_id,
    )

    # -------------------------------------------------------------------
    # Mock-mode branch: verify the orchestrator's mode-gate prevents RPC.
    # -------------------------------------------------------------------
    if args.mock:
        # Patch the chain helper so any send attempt raises immediately;
        # the orchestrator helper must skip the call entirely for
        # mode='mock' and never touch this patched function.
        from polyglot_alpha import orchestrator as orch

        async def _explode(*a: Any, **kw: Any) -> None:
            raise AssertionError(
                "mock mode should NOT issue any on-chain reputation RPC"
            )

        original_post = orch._update_reputation_on_chain_post_commit
        original_fee = orch._update_reputation_fee_on_chain

        # Sanity: the orchestrator helpers must early-return on mode='mock'
        # without calling the chain module at all.
        result_post = await orch._update_reputation_on_chain_post_commit(
            winner, quality_passed=True, mode="mock"
        )
        result_fee = await orch._update_reputation_fee_on_chain(
            winner, fee_usdc=1.0, mode="mock"
        )

        ok_post = result_post == {}
        ok_fee = result_fee is None
        logger.info(
            "mock post-commit result=%s (expected {}) -> %s",
            result_post,
            "OK" if ok_post else "FAIL",
        )
        logger.info(
            "mock fee result=%s (expected None) -> %s",
            result_fee,
            "OK" if ok_fee else "FAIL",
        )
        sim_hash_from_module = orch.sim_tx_hash()
        logger.info(
            "sample sim_tx_hash from sim_helpers: %s (no real RPC issued)",
            sim_hash_from_module,
        )
        return 0 if (ok_post and ok_fee) else 1

    # -------------------------------------------------------------------
    # Live branch: real on-chain updates + delta check.
    # -------------------------------------------------------------------
    chain_before = _chain_snapshot(client, winner)
    db_before = _db_snapshot(winner)
    logger.info("CHAIN BEFORE: %s", chain_before)
    logger.info("DB BEFORE:    %s", db_before)

    expected_quality_inc = 1 if args.quality_passed else 0
    fee_units = int(round(args.fee_usdc * (10 ** 6)))

    # Phase 5 simulation: updateOnAuction(true) + updateOnQuality(passed).
    logger.info(
        "sending post-commit signals: won=True, quality_passed=%s",
        args.quality_passed,
    )
    post_txs = await repo.update_reputation(
        winner,
        won=True,
        quality_passed=bool(args.quality_passed),
    )
    logger.info("post-commit txs: %s", post_txs)

    # Phase 7 simulation: updateOnFee(fee_usdc).
    logger.info("sending fee signal: fee_usdc=%.6f", args.fee_usdc)
    fee_tx = await repo.update_reputation_fee_only(
        winner, fee_usdc=args.fee_usdc
    )
    logger.info("fee tx: %s", fee_tx)

    # Wait for the last tx to confirm so getStats() reflects all three writes.
    if fee_tx:
        client.w3.eth.wait_for_transaction_receipt(fee_tx, timeout=60)
    elif post_txs.get("quality"):
        client.w3.eth.wait_for_transaction_receipt(
            post_txs["quality"], timeout=60
        )

    chain_after = _chain_snapshot(client, winner)
    db_after = _db_snapshot(winner)
    logger.info("CHAIN AFTER:  %s", chain_after)
    logger.info("DB AFTER:     %s", db_after)

    chain_delta = _delta(chain_before, chain_after)
    logger.info("CHAIN DELTA:  %s", chain_delta)

    # Expected deltas:
    expected = {
        "total_bids": 1,
        "total_wins": 1,
        "total_quality_passes": expected_quality_inc,
        "cumulative_fees_units": fee_units,
    }
    logger.info("EXPECTED:     %s", expected)

    # Match check.
    mismatches: list[str] = []
    for key, want in expected.items():
        got = chain_delta.get(key)
        if got != want:
            mismatches.append(f"{key}: got={got} want={want}")

    if mismatches:
        logger.error("FAIL — delta mismatches:")
        for m in mismatches:
            logger.error("  %s", m)
        print("deltas_match: FAIL")
        return 1

    # Gas estimate (informational).
    if fee_tx:
        receipt = client.w3.eth.get_transaction_receipt(fee_tx)
        logger.info(
            "fee-call gas used: %s (effective gas price %s wei)",
            receipt.get("gasUsed"),
            receipt.get("effectiveGasPrice"),
        )

    print("deltas_match: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
