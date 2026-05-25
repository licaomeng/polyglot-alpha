"""Deploy the PolyglotAlpha v2 contract suite to Arc testnet.

Deploys (in dependency order):

    1. ReputationRegistry  (no constructor args)
    2. TranslationAuction  (usdc, reputationRegistry)
    3. BuilderFeeRouter    (usdc, reputationRegistry)
    4. JudgePanel          (usdc)                  -- NEW (README §5.6 / §5.22)

QuestionRegistry is **not** redeployed by default — it has no incoming wiring
to the contracts in this MR (per README §5.7 it is its own provenance log,
written to from the orchestrator's settlement step). To redeploy it pass
``--include-question-registry``.

After deployment the script wires up authorization:

    reputation.setAuthorized(translationAuction, true)
    reputation.setAuthorized(builderFeeRouter,   true)
    reputation.setAuthorized(judgePanel,         true)   -- so slashReputation
                                                            can be invoked

And it prints copy-pasteable ``.env`` lines.

Usage::

    .venv/bin/python scripts/deploy_all_contracts.py

Environment::

    ARC_TESTNET_RPC                  (default https://rpc.testnet.arc.network)
    ARC_CHAIN_ID                     (default 5042002)
    HACKATHON_WALLET_PRIVATE_KEY     (required)
    ARC_TESTNET_USDC_ADDRESS         (required)
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


def load_artifact(name: str) -> dict:
    """Load a Foundry artifact (ABI + bytecode) from contracts/out/."""
    path = FOUNDRY_OUT / f"{name}.sol" / f"{name}.json"
    with path.open("r", encoding="utf-8") as fh:
        art = json.load(fh)
    return {
        "abi": art["abi"],
        "bytecode": art["bytecode"]["object"],
    }


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


def deploy_contract(
    w3: Web3,
    account,
    chain_id: int,
    name: str,
    constructor_args: tuple = (),
) -> tuple[str, int]:
    """Deploy a single contract; returns (address, gas_used)."""
    art = load_artifact(name)
    contract = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    nonce = w3.eth.get_transaction_count(account.address)
    txn = contract.constructor(*constructor_args).build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "chainId": chain_id,
            "gasPrice": w3.eth.gas_price,
            "gas": 4_500_000,
        }
    )
    print(f"  -> deploying {name}({', '.join(map(str, constructor_args))}) ...")
    receipt = send_signed(w3, txn, account)
    addr = receipt.contractAddress
    print(f"     deployed at {addr} (gas {receipt.gasUsed})")
    return addr, receipt.gasUsed


def authorize(
    w3: Web3,
    account,
    chain_id: int,
    rep_address: str,
    rep_abi: list,
    who: str,
    label: str,
) -> int:
    """Call ReputationRegistry.setAuthorized(who, true); returns gas used."""
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy PolyglotAlpha v2 contracts")
    parser.add_argument(
        "--include-question-registry",
        action="store_true",
        help="Also redeploy QuestionRegistry (not normally required for §5.6 corrections).",
    )
    args = parser.parse_args()

    pk = os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
    if not pk:
        print("ERROR: HACKATHON_WALLET_PRIVATE_KEY is required", file=sys.stderr)
        return 2
    usdc_address = os.environ.get(
        "ARC_TESTNET_USDC_ADDRESS",
        "0x477fC4C3DcC87C3Ceb13adc931F6bBeDAcCa391D",
    )
    rpc_url = os.environ.get("ARC_TESTNET_RPC", "https://rpc.testnet.arc.network")
    chain_id = int(os.environ.get("ARC_CHAIN_ID", "5042002"))

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        print(f"ERROR: cannot reach Arc RPC at {rpc_url}", file=sys.stderr)
        return 3

    account = Account.from_key(pk)
    print(f"deployer: {account.address}")
    print(
        f"balance:  {w3.from_wei(w3.eth.get_balance(account.address), 'ether')} ETH-equivalent"
    )
    print(f"chain id: {chain_id}")
    print(f"usdc:     {usdc_address}")
    print()

    deployed: dict[str, str] = {}
    gas_used: dict[str, int] = {}

    # 1. ReputationRegistry
    rep_addr, gas = deploy_contract(
        w3, account, chain_id, "ReputationRegistry"
    )
    deployed["ReputationRegistry"] = rep_addr
    gas_used["ReputationRegistry"] = gas

    # 2. TranslationAuction
    auct_addr, gas = deploy_contract(
        w3,
        account,
        chain_id,
        "TranslationAuction",
        (Web3.to_checksum_address(usdc_address), Web3.to_checksum_address(rep_addr)),
    )
    deployed["TranslationAuction"] = auct_addr
    gas_used["TranslationAuction"] = gas

    # 3. BuilderFeeRouter
    router_addr, gas = deploy_contract(
        w3,
        account,
        chain_id,
        "BuilderFeeRouter",
        (Web3.to_checksum_address(usdc_address), Web3.to_checksum_address(rep_addr)),
    )
    deployed["BuilderFeeRouter"] = router_addr
    gas_used["BuilderFeeRouter"] = gas

    # 4. JudgePanel  (NEW per §5.6 / §5.22)
    panel_addr, gas = deploy_contract(
        w3, account, chain_id, "JudgePanel",
        (Web3.to_checksum_address(usdc_address),),
    )
    deployed["JudgePanel"] = panel_addr
    gas_used["JudgePanel"] = gas

    if args.include_question_registry:
        qr_addr, gas = deploy_contract(w3, account, chain_id, "QuestionRegistry")
        deployed["QuestionRegistry"] = qr_addr
        gas_used["QuestionRegistry"] = gas

    # 5. Wire up authorization on ReputationRegistry so the three downstream
    #    contracts can push updates / slash. The deployer is already authorized.
    rep_art = load_artifact("ReputationRegistry")
    for label, addr in (
        ("TranslationAuction", auct_addr),
        ("BuilderFeeRouter", router_addr),
        ("JudgePanel", panel_addr),
    ):
        gas = authorize(
            w3, account, chain_id, rep_addr, rep_art["abi"], addr, label
        )
        gas_used[f"auth({label})"] = gas

    final_balance = w3.from_wei(w3.eth.get_balance(account.address), "ether")

    print()
    print("=== Deployment summary ===")
    for name, addr in deployed.items():
        print(f"  {name}: {addr}  (gas {gas_used[name]})")
    print()
    print("=== Gas used (authorization) ===")
    for label, g in gas_used.items():
        if label.startswith("auth("):
            print(f"  {label}: {g}")
    print()
    total_gas = sum(gas_used.values())
    print(f"total gas: {total_gas}")
    print(f"final balance: {final_balance} ETH-equivalent")
    print()
    print("=== Suggested .env updates ===")
    print(f"REPUTATION_REGISTRY_ADDRESS={rep_addr}")
    print(f"TRANSLATION_AUCTION_ADDRESS={auct_addr}")
    print(f"BUILDER_FEE_ROUTER_ADDRESS={router_addr}")
    print(f"JUDGE_PANEL_ADDRESS={panel_addr}")
    if "QuestionRegistry" in deployed:
        print(f"QUESTION_REGISTRY_ADDRESS={deployed['QuestionRegistry']}")
    print()

    # Dump a JSON file alongside for programmatic consumers.
    out_path = REPO_ROOT / "outputs" / "deployment_v2.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "chain_id": chain_id,
                "deployer": account.address,
                "usdc": usdc_address,
                "deployed_at": int(time.time()),
                "addresses": deployed,
                "gas_used": gas_used,
            },
            indent=2,
        )
    )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
