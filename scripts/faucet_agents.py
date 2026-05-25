"""Idempotent faucet for the four translator agent wallets.

Reads ``outputs/agent_wallets.json`` for the agent public addresses and
tops each one up using the operator wallet
(``HACKATHON_WALLET_PRIVATE_KEY``) until two minimum balances are met:

* Arc native (ETH-shaped) >= ``ETH_TARGET`` (default 0.05).
* MockUSDC                >= ``USDC_TARGET`` (default 20.0).

Safe to re-run: if a wallet is already above the target the script
silently skips its top-up. The final summary prints the post-faucet
balances per agent.

Usage::

    .venv/bin/python scripts/faucet_agents.py
"""

from __future__ import annotations

import argparse
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

from polyglot_alpha.agents.wallets import derive_all_wallets  # noqa: E402
from polyglot_alpha.onchain import (  # noqa: E402
    OnChainClient,
    USDC_DECIMALS,
    usdc_to_units,
)


ETH_TARGET = float(os.environ.get("AGENT_ETH_TARGET", "0.05"))
USDC_TARGET = float(os.environ.get("AGENT_USDC_TARGET", "20.0"))

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

    # Prefer ``transfer`` from the operator. If the operator's USDC
    # balance is short, fall back to ``mint`` (the deployed token is a
    # MockUSDC with permissionless mint).
    op_bal = int(
        client.usdc.functions.balanceOf(operator.address).call()
    )
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
    # Fallback: mint directly to the agent.
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
            "Top up the four agent wallets with native ETH + MockUSDC. "
            "Idempotent: skips any wallet already at or above the target."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Inspect balances and report the deltas that would be sent "
            "without broadcasting any transaction."
        ),
    )
    parser.add_argument(
        "--eth-target",
        type=float,
        default=ETH_TARGET,
        help=f"Minimum native balance per agent (default {ETH_TARGET}).",
    )
    parser.add_argument(
        "--usdc-target",
        type=float,
        default=USDC_TARGET,
        help=f"Minimum MockUSDC balance per agent (default {USDC_TARGET}).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    eth_target = float(args.eth_target)
    usdc_target = float(args.usdc_target)
    dry_run = bool(args.dry_run)

    print(
        f"[faucet] target eth={eth_target} usdc={usdc_target}"
        f"{' (DRY-RUN)' if dry_run else ''}"
    )
    operator = _operator_account()
    client = OnChainClient()
    w3 = client.w3

    op_eth_wei = w3.eth.get_balance(operator.address)
    op_eth = op_eth_wei / 1e18
    op_usdc_units = int(client.usdc.functions.balanceOf(operator.address).call())
    op_usdc = op_usdc_units / (10 ** USDC_DECIMALS)
    print(
        f"[faucet] operator={operator.address} "
        f"eth={op_eth:.6f} usdc={op_usdc:.4f}"
    )
    if op_eth < OPERATOR_RESERVE_ETH:
        print(
            f"[faucet] WARNING: operator eth {op_eth:.6f} below reserve "
            f"{OPERATOR_RESERVE_ETH:.4f}; top-ups may fail"
        )

    wallets = derive_all_wallets()
    summary_rows: list[tuple[str, str, float, float, str]] = []

    for name, wallet in wallets.items():
        addr = wallet.address
        eth_bal = w3.eth.get_balance(addr)
        usdc_bal = int(client.usdc.functions.balanceOf(addr).call())
        pre_eth = eth_bal / 1e18
        pre_usdc = usdc_bal / (10 ** USDC_DECIMALS)

        if dry_run:
            eth_delta_wei = max(int(eth_target * 1e18) - eth_bal, 0)
            usdc_delta_units = max(usdc_to_units(usdc_target) - usdc_bal, 0)
            notes_parts: list[str] = []
            if eth_delta_wei > 0:
                notes_parts.append(f"would_send_eth={eth_delta_wei / 1e18:.6f}")
            if usdc_delta_units > 0:
                notes_parts.append(
                    f"would_send_usdc={usdc_delta_units / (10 ** USDC_DECIMALS):.4f}"
                )
            notes = ", ".join(notes_parts) or "no top-up needed"
            summary_rows.append((name, addr, pre_eth, pre_usdc, notes))
            print(
                f"[faucet] {name:9s} {addr} pre eth={pre_eth:.6f} "
                f"usdc={pre_usdc:.4f}   ({notes})"
            )
            continue

        eth_changed, eth_tx = _top_up_eth(
            w3, operator, addr, eth_bal, eth_target
        )
        # Re-read post top-up so the summary is accurate.
        if eth_changed:
            time.sleep(1)
            eth_bal = w3.eth.get_balance(addr)

        usdc_changed, usdc_tx = _top_up_usdc(
            client, operator, addr, usdc_bal, usdc_target
        )
        if usdc_changed:
            time.sleep(1)
            usdc_bal = int(client.usdc.functions.balanceOf(addr).call())

        eth_float = eth_bal / 1e18
        usdc_float = usdc_bal / (10 ** USDC_DECIMALS)
        notes_parts = []
        if eth_changed:
            notes_parts.append(f"eth_tx={eth_tx}")
        if usdc_changed:
            notes_parts.append(f"usdc_tx={usdc_tx}")
        notes = ", ".join(notes_parts) or "no top-up needed"
        summary_rows.append((name, addr, eth_float, usdc_float, notes))
        print(
            f"[faucet] {name:9s} {addr} pre eth={pre_eth:.6f} "
            f"usdc={pre_usdc:.4f} -> post eth={eth_float:.6f} "
            f"usdc={usdc_float:.4f}   ({notes})"
        )

    print("\n[faucet] summary:")
    for name, addr, eth, usdc, notes in summary_rows:
        print(f"  {name:9s} {addr} eth={eth:.6f} usdc={usdc:.4f}  {notes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
