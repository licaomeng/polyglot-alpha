"""W16-B helper: fund + register the rotated v2 seeder wallets.

The legacy seeder slots (``gemini`` / ``deepseek`` / ``qwen``) carry
on-chain reputation below TranslationAuction's 0.7 gate, so every
``submitBid`` from those wallets reverts with ``"reputation gate"``.

Rotating to fresh agent names (``gemini-v2`` / ``deepseek-v2`` /
``qwen-v2``) produces wallets with a fresh ReputationRegistry score
(initial value 1.0) which sails through the gate. This script:

1. Derives the three v2 wallet addresses from
   ``HACKATHON_WALLET_PRIVATE_KEY`` (same deterministic SHA-256 derivation
   used by :mod:`polyglot_alpha.agents.wallets`).
2. Idempotently tops each one up with native ETH + MockUSDC from the
   operator wallet (skips if already above target).
3. Calls ``TranslationAuction.registerAgent()`` from each wallet
   (5 USDC stake), skipping wallets already marked ``registered``.

Safe to re-run. Prints a summary table with addresses, balances,
register tx hashes, and on-chain reputation post-register.

Usage::

    .venv/bin/python scripts/fund_seeder_wallets_v2.py
    .venv/bin/python scripts/fund_seeder_wallets_v2.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3

# Make the repo root importable without ``pip install -e .``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import polyglot_alpha  # noqa: E402 — runs the .env loader

from polyglot_alpha.agents.wallets import derive_agent_wallet  # noqa: E402
from polyglot_alpha.chain.auction_client import AuctionClient  # noqa: E402
from polyglot_alpha.onchain import (  # noqa: E402
    OnChainClient,
    USDC_DECIMALS,
    usdc_to_units,
)

# v2 seeder slots (W16-B rotation). Mirrored in
# ``polyglot_alpha.agents.wallets.AGENT_NAMES`` and the orchestrator's
# ``agent_names`` tuple.
V2_AGENT_NAMES: tuple[str, ...] = ("gemini-v2", "deepseek-v2", "qwen-v2")

ETH_TARGET = float(os.environ.get("AGENT_ETH_TARGET_V2", "0.05"))
USDC_TARGET = float(os.environ.get("AGENT_USDC_TARGET_V2", "20.0"))
STAKE_USDC = float(os.environ.get("AGENT_STAKE_USDC", "5.0"))

# Gas overhead the operator needs to keep for itself (sanity check).
OPERATOR_RESERVE_ETH = 0.01

ETH_GAS_LIMIT = 21_000
USDC_TRANSFER_GAS = 80_000
USDC_MINT_GAS = 120_000


def _operator_account() -> LocalAccount:
    pk = os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
    if not pk:
        sys.exit("HACKATHON_WALLET_PRIVATE_KEY not set; aborting")
    return Account.from_key(pk)


def _send(w3: Web3, txn: dict[str, Any], account: LocalAccount) -> str:
    signed = w3.eth.account.sign_transaction(txn, account.key)
    raw_tx = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw_tx).hex()
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        raise RuntimeError(f"tx reverted: {tx_hash}")
    return tx_hash


def _top_up_eth(
    w3: Web3,
    operator: LocalAccount,
    target_address: str,
    current_balance_wei: int,
    target_eth: float,
) -> tuple[bool, str]:
    target_wei = int(target_eth * 1e18)
    if current_balance_wei >= target_wei:
        return False, ""
    delta_wei = target_wei - current_balance_wei
    nonce = w3.eth.get_transaction_count(operator.address)
    txn = {
        "from": operator.address,
        "to": Web3.to_checksum_address(target_address),
        "value": delta_wei,
        "nonce": nonce,
        "gas": ETH_GAS_LIMIT,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    }
    tx_hash = _send(w3, txn, operator)
    return True, tx_hash


def _top_up_usdc(
    client: OnChainClient,
    operator: LocalAccount,
    target_address: str,
    current_units: int,
    target_usdc: float,
) -> tuple[bool, str]:
    target_units = usdc_to_units(target_usdc)
    if current_units >= target_units:
        return False, ""
    delta_units = target_units - current_units

    op_bal = int(client.usdc.functions.balanceOf(operator.address).call())
    nonce = client.w3.eth.get_transaction_count(operator.address)
    if op_bal >= delta_units:
        txn = client.usdc.functions.transfer(
            Web3.to_checksum_address(target_address), delta_units
        ).build_transaction(
            {
                "from": operator.address,
                "nonce": nonce,
                "gas": USDC_TRANSFER_GAS,
                "gasPrice": client.w3.eth.gas_price,
                "chainId": client.config.chain_id,
            }
        )
        tx_hash = _send(client.w3, txn, operator)
        return True, tx_hash
    try:
        txn = client.usdc.functions.mint(
            Web3.to_checksum_address(target_address), delta_units
        ).build_transaction(
            {
                "from": operator.address,
                "nonce": nonce,
                "gas": USDC_MINT_GAS,
                "gasPrice": client.w3.eth.gas_price,
                "chainId": client.config.chain_id,
            }
        )
        tx_hash = _send(client.w3, txn, operator)
        return True, tx_hash
    except Exception as exc:  # pragma: no cover - real-RPC dependent
        return False, f"mint failed: {exc!s}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fund + register the W16-B v2 seeder wallets. "
            "Idempotent: skips any wallet already at/above target balance "
            "and already-registered agents."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect balances + registration state, do not send any tx.",
    )
    parser.add_argument(
        "--eth-target", type=float, default=ETH_TARGET,
        help=f"Minimum native balance per agent (default {ETH_TARGET}).",
    )
    parser.add_argument(
        "--usdc-target", type=float, default=USDC_TARGET,
        help=f"Minimum MockUSDC balance per agent (default {USDC_TARGET}).",
    )
    parser.add_argument(
        "--stake-usdc", type=float, default=STAKE_USDC,
        help=f"Stake amount in USDC for registerAgent (default {STAKE_USDC}).",
    )
    parser.add_argument(
        "--skip-register",
        action="store_true",
        help="Only fund; do not call registerAgent (useful when only re-funding).",
    )
    return parser.parse_args()


async def _register_one(
    auction_client: AuctionClient,
    onchain: OnChainClient,
    name: str,
    pk: str,
    address: str,
    stake_usdc: float,
) -> tuple[str, str]:
    """Call registerAgent if not already registered. Returns (tx_or_status, note)."""

    if onchain.is_registered(address):
        return ("already_registered", "skipped")
    tx_hash = await auction_client.register_agent(agent_pk=pk, stake_usdc=stake_usdc)
    # Confirm receipt
    receipt = onchain.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    status = int(getattr(receipt, "status", 0))
    if status != 1:
        return (tx_hash, "REVERTED")
    return (tx_hash, "ok")


async def _async_main(args: argparse.Namespace) -> int:
    eth_target = float(args.eth_target)
    usdc_target = float(args.usdc_target)
    stake_usdc = float(args.stake_usdc)
    dry_run = bool(args.dry_run)

    print(
        f"[fund-v2] target eth={eth_target} usdc={usdc_target} "
        f"stake={stake_usdc}{' (DRY-RUN)' if dry_run else ''}"
    )
    operator = _operator_account()
    op_pk = operator.key.hex()
    if not op_pk.startswith("0x"):
        op_pk = "0x" + op_pk
    client = OnChainClient()
    w3 = client.w3

    op_eth_wei = w3.eth.get_balance(operator.address)
    op_eth = op_eth_wei / 1e18
    op_usdc_units = int(client.usdc.functions.balanceOf(operator.address).call())
    op_usdc = op_usdc_units / (10 ** USDC_DECIMALS)
    print(
        f"[fund-v2] operator={operator.address} "
        f"eth={op_eth:.6f} usdc={op_usdc:.4f}"
    )
    if op_eth < OPERATOR_RESERVE_ETH:
        print(
            f"[fund-v2] WARNING: operator eth {op_eth:.6f} below reserve "
            f"{OPERATOR_RESERVE_ETH:.4f}; top-ups may fail"
        )

    # Pre-flight USDC sufficiency check for the stake legs.
    total_usdc_needed_for_topup = 0.0
    wallets: dict[str, Any] = {}
    for name in V2_AGENT_NAMES:
        w = derive_agent_wallet(op_pk, name)
        wallets[name] = w
        cur_usdc = int(client.usdc.functions.balanceOf(w.address).call())
        delta = max(usdc_to_units(usdc_target) - cur_usdc, 0)
        total_usdc_needed_for_topup += delta / (10 ** USDC_DECIMALS)
    print(
        f"[fund-v2] usdc needed for top-up across 3 agents: "
        f"{total_usdc_needed_for_topup:.4f} (op has {op_usdc:.4f})"
    )

    rows: list[dict[str, Any]] = []
    auction_client = AuctionClient(onchain=client)

    for name in V2_AGENT_NAMES:
        w = wallets[name]
        addr = w.address

        eth_bal = w3.eth.get_balance(addr)
        usdc_bal = int(client.usdc.functions.balanceOf(addr).call())
        pre_eth = eth_bal / 1e18
        pre_usdc = usdc_bal / (10 ** USDC_DECIMALS)
        pre_rep = client.get_reputation(addr)
        pre_reg = client.is_registered(addr)

        eth_tx: str = ""
        usdc_tx: str = ""
        reg_tx: str = ""
        reg_status: str = ""

        if dry_run:
            eth_delta_wei = max(int(eth_target * 1e18) - eth_bal, 0)
            usdc_delta_units = max(usdc_to_units(usdc_target) - usdc_bal, 0)
            print(
                f"[fund-v2] {name:13s} {addr} eth={pre_eth:.6f} "
                f"usdc={pre_usdc:.4f} rep={pre_rep:.4f} registered={pre_reg}  "
                f"would_send_eth={eth_delta_wei/1e18:.6f} "
                f"would_send_usdc={usdc_delta_units/(10**USDC_DECIMALS):.4f} "
                f"would_register={not pre_reg and not args.skip_register}"
            )
            rows.append(
                {
                    "name": name, "address": addr,
                    "eth": pre_eth, "usdc": pre_usdc,
                    "reputation": pre_rep, "registered": pre_reg,
                    "eth_tx": "", "usdc_tx": "", "reg_tx": "", "reg_status": "dry-run",
                }
            )
            continue

        # ------ ETH top-up
        eth_changed, eth_tx = _top_up_eth(
            w3, operator, addr, eth_bal, eth_target
        )
        if eth_changed:
            time.sleep(1)
            eth_bal = w3.eth.get_balance(addr)

        # ------ USDC top-up
        usdc_changed, usdc_tx = _top_up_usdc(
            client, operator, addr, usdc_bal, usdc_target
        )
        if usdc_changed:
            time.sleep(1)
            usdc_bal = int(client.usdc.functions.balanceOf(addr).call())

        # ------ Register on auction
        if not args.skip_register:
            try:
                reg_tx, reg_status = await _register_one(
                    auction_client, client, name, w.private_key, addr, stake_usdc
                )
            except Exception as exc:
                reg_tx = ""
                reg_status = f"FAILED: {exc!s}"
        else:
            reg_status = "skipped (--skip-register)"

        # Re-read post-register state
        post_rep = client.get_reputation(addr)
        post_reg = client.is_registered(addr)
        post_eth = w3.eth.get_balance(addr) / 1e18
        post_usdc = int(client.usdc.functions.balanceOf(addr).call()) / (10 ** USDC_DECIMALS)

        print(
            f"[fund-v2] {name:13s} {addr}"
            f"\n         pre  eth={pre_eth:.6f} usdc={pre_usdc:.4f} rep={pre_rep:.4f} registered={pre_reg}"
            f"\n         post eth={post_eth:.6f} usdc={post_usdc:.4f} rep={post_rep:.4f} registered={post_reg}"
            f"\n         eth_tx={eth_tx or '(skipped)'}"
            f"\n         usdc_tx={usdc_tx or '(skipped)'}"
            f"\n         reg_tx={reg_tx or '(none)'}  status={reg_status}"
        )

        rows.append(
            {
                "name": name, "address": addr,
                "eth": post_eth, "usdc": post_usdc,
                "reputation": post_rep, "registered": post_reg,
                "eth_tx": eth_tx, "usdc_tx": usdc_tx,
                "reg_tx": reg_tx, "reg_status": reg_status,
            }
        )

    print("\n[fund-v2] summary:")
    print(f"  {'name':13s} {'address':42s}  {'eth':>10s}  {'usdc':>8s}  {'rep':>6s}  {'reg':>5s}  reg_tx")
    for r in rows:
        print(
            f"  {r['name']:13s} {r['address']:42s}  "
            f"{r['eth']:10.6f}  {r['usdc']:8.4f}  "
            f"{r['reputation']:6.4f}  {str(r['registered']):>5s}  {r['reg_tx']}"
        )
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
