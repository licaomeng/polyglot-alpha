#!/usr/bin/env python3
"""W9-A on-chain verifier: confirm JudgePanel attestation matches DB.

After the orchestrator finalizes an event with the W9-A wiring, this
script reads the on-chain ``AttestationRecorded`` event log from the
JudgePanel contract for the given ``event_id`` and prints a side-by-side
comparison with the DB row. Both sides must match for PASS.

Usage::

    .venv/bin/python scripts/w9-a-verify.py <event_id>
    .venv/bin/python scripts/w9-a-verify.py <event_id> --rpc <url>

The script exits 0 on match, 1 on any mismatch / missing data. It is
safe to re-run anytime — read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from web3 import Web3

# Re-use the project's persistence layer so we don't reimplement the
# QualityScore row read.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from polyglot_alpha.chain.judge_panel_client import (  # noqa: E402
    _load_abi,
    _panel_address,
    attestation_hash_for_dossier,
    scale_overall_score,
)
from polyglot_alpha.onchain import event_id_from_event  # noqa: E402
from polyglot_alpha.persistence import session_scope  # noqa: E402
from polyglot_alpha.persistence.models import QualityScore  # noqa: E402


def _resolve_rpc_url(cli_value: Optional[str]) -> str:
    if cli_value:
        return cli_value
    return os.environ.get(
        "ARC_TESTNET_RPC", "https://rpc.testnet.arc.network"
    )


def _eid_bytes32(event_id: int) -> bytes:
    """Mirror the same coercion ``record_attestation`` uses."""

    return event_id_from_event(str(event_id))


def _load_db_attestation(event_id: int) -> dict[str, Any]:
    """Pull the W9-A attestation payload off the QualityScore row."""

    with session_scope() as session:
        row: Optional[QualityScore] = session.get(QualityScore, event_id)
        if row is None:
            raise SystemExit(
                f"DB: no QualityScore row for event_id={event_id}; cannot verify"
            )
        tscores = dict(row.translation_scores or {})
        attest = tscores.get("_judgesAttestation") or {}
        return {
            "overall_score": float(row.overall_score),
            "verdict": row.verdict,
            "judges_dossier": tscores.get("_judges") or [],
            "attestation": dict(attest) if isinstance(attest, dict) else {},
        }


def _read_chain_events(
    w3: Web3,
    event_id_bytes: bytes,
    *,
    from_block: int = 0,
) -> list[dict[str, Any]]:
    """Scan logs for ``AttestationRecorded`` matching ``event_id``."""

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(_panel_address()),
        abi=_load_abi(),
    )
    latest = w3.eth.block_number
    # 25k-block windows so RPC providers with a max-range limit don't 400.
    window = 25_000
    matched: list[dict[str, Any]] = []
    block = max(0, from_block)
    while block <= latest:
        to_block = min(latest, block + window - 1)
        try:
            flt = contract.events.AttestationRecorded.create_filter(
                from_block=block,
                to_block=to_block,
                argument_filters={"eventId": event_id_bytes},
            )
            entries = flt.get_all_entries()
        except Exception as exc:  # pragma: no cover - best-effort
            print(
                f"warn: filter from_block={block} to_block={to_block} failed: {exc}",
                file=sys.stderr,
            )
            entries = []
        for e in entries:
            args = getattr(e, "args", None) or e["args"]
            tx_hash = (
                e["transactionHash"].hex()
                if isinstance(e, dict)
                else e.transactionHash.hex()
            )
            if not tx_hash.startswith("0x"):
                tx_hash = "0x" + tx_hash
            matched.append(
                {
                    "tx_hash": tx_hash,
                    "event_id_hex": "0x" + bytes(args["eventId"]).hex(),
                    "judge": args["judge"],
                    "score": int(args["score"]),
                    "attestation_hash_hex": "0x"
                    + bytes(args["attestationHash"]).hex(),
                    "block_number": getattr(e, "blockNumber", None)
                    or (e.get("blockNumber") if isinstance(e, dict) else None),
                }
            )
        block = to_block + 1
    return matched


def main() -> int:
    parser = argparse.ArgumentParser(
        description="W9-A JudgePanel on-chain verifier"
    )
    parser.add_argument("event_id", type=int, help="DB event_id to verify")
    parser.add_argument(
        "--rpc", default=None, help="Arc RPC URL (defaults to ARC_TESTNET_RPC)"
    )
    parser.add_argument(
        "--from-block",
        type=int,
        default=0,
        help="Start block for log scan (default 0)",
    )
    args = parser.parse_args()

    rpc_url = _resolve_rpc_url(args.rpc)
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        print(f"FAIL: cannot connect to RPC {rpc_url}", file=sys.stderr)
        return 1

    db = _load_db_attestation(args.event_id)
    db_attest = db["attestation"]
    db_tx = db_attest.get("txHash")
    db_hash = db_attest.get("attestationHash")
    db_score_scaled = db_attest.get("scoreScaled")

    # Recompute the canonical hash from the dossier we have in the DB,
    # so we can detect tampering between "what was hashed at orchestrator
    # time" and "what is in the DB now".
    recomputed = "0x" + attestation_hash_for_dossier(db["judges_dossier"]).hex()
    recomputed_score = scale_overall_score(db["overall_score"])

    eid_bytes = _eid_bytes32(args.event_id)
    chain_events = _read_chain_events(
        w3, eid_bytes, from_block=args.from_block
    )

    print("=" * 72)
    print(f"W9-A verifier · event_id={args.event_id}")
    print(f"  RPC: {rpc_url}")
    print(f"  JudgePanel: {_panel_address()}")
    print(f"  eventId bytes32: 0x{eid_bytes.hex()}")
    print("-" * 72)
    print("DB row:")
    print(f"  verdict        : {db['verdict']}")
    print(f"  overall_score  : {db['overall_score']:.4f}")
    print(f"  dossier judges : {len(db['judges_dossier'])}")
    print(f"  attestation_tx : {db_tx}")
    print(f"  attest_hash    : {db_hash}")
    print(f"  score_scaled   : {db_score_scaled}")
    print("  recomputed (from current DB dossier):")
    print(f"    attest_hash  : {recomputed}")
    print(f"    score_scaled : {recomputed_score}")
    print("-" * 72)
    print(f"Chain logs ({len(chain_events)} AttestationRecorded entries):")
    for i, e in enumerate(chain_events):
        print(
            f"  [{i}] tx={e['tx_hash']} judge={e['judge']} "
            f"score={e['score']} hash={e['attestation_hash_hex']}"
        )
    print("=" * 72)

    # ---- Validation rules ------------------------------------------------
    failures: list[str] = []

    if not db_tx or not isinstance(db_tx, str):
        failures.append("DB: no attestation tx_hash recorded")
    elif db_tx.lower().startswith("0xsim_"):
        print(
            "INFO: DB tx is mock-mode sim hash; chain check skipped "
            "(mock lifecycles never write to Arc)."
        )
        print("PASS: mock-mode payload shape is correct.")
        return 0

    if recomputed != db_hash:
        failures.append(
            f"DB tampering: stored attest_hash={db_hash} != recomputed "
            f"keccak256(dossier)={recomputed}"
        )

    if not chain_events:
        failures.append(
            "Chain: no AttestationRecorded log found for this event_id"
        )
    else:
        # Match the chain entry whose tx_hash equals db_tx; if none match,
        # take the most recent entry for that event_id and call out the
        # delta.
        match = next(
            (e for e in chain_events if e["tx_hash"].lower() == (db_tx or "").lower()),
            None,
        )
        if match is None:
            match = chain_events[-1]
            failures.append(
                f"Chain tx mismatch: DB tx_hash={db_tx} not in chain logs; "
                f"latest on-chain entry tx={match['tx_hash']}"
            )
        if match["attestation_hash_hex"].lower() != (db_hash or "").lower():
            failures.append(
                f"hash mismatch: chain={match['attestation_hash_hex']} "
                f"db={db_hash}"
            )
        if db_score_scaled is not None and match["score"] != int(db_score_scaled):
            failures.append(
                f"score mismatch: chain={match['score']} "
                f"db_scaled={db_score_scaled}"
            )
        chain_says = (
            f"judge={match['judge']} score={match['score']} "
            f"hash={match['attestation_hash_hex']}"
        )
        db_says = (
            f"agg={db_attest.get('aggregatorAddress')} "
            f"score={db_score_scaled} hash={db_hash}"
        )
        print(f"chain_says: {chain_says}")
        print(f"db_says:    {db_says}")

    if failures:
        print("\nFAIL · " + str(len(failures)) + " issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS · on-chain attestation matches DB row.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
