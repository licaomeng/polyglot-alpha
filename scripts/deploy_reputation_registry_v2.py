"""Deploy the **v2** ReputationRegistry contract to Arc testnet (W14-CONTRACT-PREP).

The v2 contract fixes two issues in the deployed v1 (`REPUTATION_REGISTRY_ADDRESS`):

  * β: ``_fillSignal`` now rescales 6-decimal USDC base units to 1e18 fixed-point
    before the ln() input, so ``fillSignal`` actually spans [0.5, 2.0] instead of
    collapsing to ``FILL_MIN=0.5`` for any realistic fee.
  * α: initial score on first touch is ``HALF=0.5e18`` instead of ``ONE=1e18`` so
    the first ``_recompute`` does not strictly decrease the score from a maxed-out
    prior.

Idempotency
-----------
By default the script **does not deploy** — it only prints what *would* happen.
Pass ``--confirm`` to actually broadcast the deployment transaction.

If ``REPUTATION_REGISTRY_V2_ADDRESS`` is set in the environment and the
contract exists at that address (non-empty bytecode), the script exits early —
the v2 contract is considered already deployed and the script is a no-op.

Authorization
-------------
After deployment, the script calls ``setAuthorized(operator, true)`` for the
operator EOA so the orchestrator can immediately push state. To wire the
downstream contracts (``TranslationAuction`` / ``BuilderFeeRouter`` /
``JudgePanel``), pass their addresses via the matching env vars (or CLI flags)
and the script will authorize each in turn.

Environment
-----------
* ``HACKATHON_WALLET_PRIVATE_KEY`` — operator private key (required)
* ``ARC_TESTNET_RPC``              — RPC URL (default https://rpc.testnet.arc.network)
* ``ARC_CHAIN_ID``                 — chain id (default 5042002)
* ``REPUTATION_REGISTRY_V2_ADDRESS`` — set to skip deploy (idempotency)
* ``TRANSLATION_AUCTION_ADDRESS``    — optional: authorize after deploy
* ``BUILDER_FEE_ROUTER_ADDRESS``     — optional: authorize after deploy
* ``JUDGE_PANEL_ADDRESS``            — optional: authorize after deploy

Usage::

    # dry-run (default)
    .venv/bin/python scripts/deploy_reputation_registry_v2.py

    # actually deploy
    .venv/bin/python scripts/deploy_reputation_registry_v2.py --confirm

    # deploy + explicit downstream authorizations
    .venv/bin/python scripts/deploy_reputation_registry_v2.py --confirm \\
        --authorize-auction 0x... --authorize-router 0x... --authorize-judge 0x...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3


REPO_ROOT = Path(__file__).resolve().parents[1]
FOUNDRY_OUT = REPO_ROOT / "contracts" / "out"
ARTIFACT_NAME = "ReputationRegistry"


def load_artifact(name: str) -> dict:
    """Load a Foundry artifact (ABI + bytecode) from contracts/out/."""
    path = FOUNDRY_OUT / f"{name}.sol" / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Foundry artifact not found at {path}. Run `cd contracts && forge build` first."
        )
    with path.open("r", encoding="utf-8") as fh:
        art = json.load(fh)
    return {"abi": art["abi"], "bytecode": art["bytecode"]["object"]}


def wait_receipt(w3: Web3, tx_hash: bytes, timeout: int = 180) -> dict:
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
    if receipt.status != 1:
        raise RuntimeError(f"tx failed: {tx_hash.hex()}")
    return receipt


def send_signed(w3: Web3, txn: dict, account) -> dict:
    signed = w3.eth.account.sign_transaction(txn, account.key)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    return wait_receipt(w3, tx_hash)


def deploy_registry(w3: Web3, account, chain_id: int) -> tuple[str, int]:
    art = load_artifact(ARTIFACT_NAME)
    contract = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    nonce = w3.eth.get_transaction_count(account.address)
    txn = contract.constructor().build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "chainId": chain_id,
            "gasPrice": w3.eth.gas_price,
            "gas": 4_500_000,
        }
    )
    print(f"  -> deploying {ARTIFACT_NAME} v2 ...")
    receipt = send_signed(w3, txn, account)
    addr = receipt.contractAddress
    print(f"     deployed at {addr} (gas {receipt.gasUsed})")
    return addr, receipt.gasUsed


def authorize(
    w3: Web3, account, chain_id: int, rep_address: str, rep_abi: list, who: str, label: str
) -> int:
    contract = w3.eth.contract(address=Web3.to_checksum_address(rep_address), abi=rep_abi)
    nonce = w3.eth.get_transaction_count(account.address)
    txn = contract.functions.setAuthorized(
        Web3.to_checksum_address(who), True
    ).build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "chainId": chain_id,
            "gasPrice": w3.eth.gas_price,
            "gas": 120_000,
        }
    )
    print(f"  -> setAuthorized({label} = {who}, true) ...")
    receipt = send_signed(w3, txn, account)
    print(f"     authorized (gas {receipt.gasUsed})")
    return receipt.gasUsed


def already_deployed(w3: Web3, address: str | None) -> bool:
    """Return True if `address` is non-empty and contains contract code."""
    if not address:
        return False
    try:
        addr = Web3.to_checksum_address(address)
    except (ValueError, TypeError):
        return False
    code = w3.eth.get_code(addr)
    return code is not None and len(code) > 0 and code != b"\x00"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy v2 ReputationRegistry (β + α fix). Dry-run unless --confirm."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually broadcast deployment + authorization transactions. "
             "Without this flag the script is a dry-run.",
    )
    parser.add_argument(
        "--authorize-auction",
        default=os.environ.get("TRANSLATION_AUCTION_ADDRESS"),
        help="Address of TranslationAuction to authorize post-deploy.",
    )
    parser.add_argument(
        "--authorize-router",
        default=os.environ.get("BUILDER_FEE_ROUTER_ADDRESS"),
        help="Address of BuilderFeeRouter to authorize post-deploy.",
    )
    parser.add_argument(
        "--authorize-judge",
        default=os.environ.get("JUDGE_PANEL_ADDRESS"),
        help="Address of JudgePanel to authorize post-deploy.",
    )
    args = parser.parse_args()

    pk = os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
    if not pk:
        print("ERROR: HACKATHON_WALLET_PRIVATE_KEY is required", file=sys.stderr)
        return 2
    rpc_url = os.environ.get("ARC_TESTNET_RPC", "https://rpc.testnet.arc.network")
    chain_id = int(os.environ.get("ARC_CHAIN_ID", "5042002"))
    existing_v2 = os.environ.get("REPUTATION_REGISTRY_V2_ADDRESS")

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        print(f"ERROR: cannot reach Arc RPC at {rpc_url}", file=sys.stderr)
        return 3

    account = Account.from_key(pk)
    print(f"deployer:        {account.address}")
    print(f"chain id:        {chain_id}")
    print(f"RPC:             {rpc_url}")
    print(f"existing v2 env: {existing_v2 or '(unset)'}")
    print(
        f"balance:         {w3.from_wei(w3.eth.get_balance(account.address), 'ether')} ETH-equivalent"
    )
    print()

    # Idempotency check
    if already_deployed(w3, existing_v2):
        print(f"v2 already deployed at {existing_v2} — no action taken (idempotent).")
        return 0

    if not args.confirm:
        print("DRY-RUN — would do the following (re-run with --confirm to broadcast):")
        print(f"  1. deploy ReputationRegistry (constructor takes no args)")
        print(f"  2. setAuthorized({account.address}, true)  # operator EOA")
        for label, addr in (
            ("TranslationAuction", args.authorize_auction),
            ("BuilderFeeRouter", args.authorize_router),
            ("JudgePanel", args.authorize_judge),
        ):
            if addr:
                print(f"  3. setAuthorized({addr}, true)  # {label}")
        return 0

    # 1. Deploy
    rep_addr, deploy_gas = deploy_registry(w3, account, chain_id)

    # 2. Authorize operator (constructor already does this — deployer is auto-authorized,
    #    but we re-issue defensively in case the wallet differs from msg.sender).
    rep_art = load_artifact(ARTIFACT_NAME)
    auth_gas = {}

    # The deployer is the operator EOA and is already authorized by the constructor,
    # so we skip a redundant setAuthorized(operator) call. But we still authorize
    # any downstream contracts the user passed in.
    for label, addr in (
        ("TranslationAuction", args.authorize_auction),
        ("BuilderFeeRouter", args.authorize_router),
        ("JudgePanel", args.authorize_judge),
    ):
        if addr:
            auth_gas[f"auth({label})"] = authorize(
                w3, account, chain_id, rep_addr, rep_art["abi"], addr, label
            )

    final_balance = w3.from_wei(w3.eth.get_balance(account.address), "ether")

    print()
    print("=== Deployment summary ===")
    print(f"  ReputationRegistry (v2): {rep_addr}  (deploy gas {deploy_gas})")
    for label, g in auth_gas.items():
        print(f"  {label}: {g}")
    print(f"  final balance:           {final_balance} ETH-equivalent")
    print()
    print("=== Suggested .env update ===")
    print(f"REPUTATION_REGISTRY_V2_ADDRESS={rep_addr}")
    print(
        "  # Once verified, update REPUTATION_REGISTRY_ADDRESS to point at this "
        "address and remove the V2 suffix."
    )

    # Dump a JSON artifact alongside the existing deployment_v2.json
    out_path = REPO_ROOT / "outputs" / "deployment_reputation_v2.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "chain_id": chain_id,
                "deployer": account.address,
                "deployed_at": int(time.time()),
                "address": rep_addr,
                "deploy_gas": deploy_gas,
                "auth_gas": auth_gas,
                "notes": "W14-CONTRACT-PREP β + α fix",
            },
            indent=2,
        )
    )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
