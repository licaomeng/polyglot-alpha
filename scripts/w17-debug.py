"""W17-A diagnostic: dump on-chain reputation/registration state for all 6 seeder addresses.

Reads:
  - REPUTATION_REGISTRY_ADDRESS / TRANSLATION_AUCTION_ADDRESS / ARC_TESTNET_USDC_ADDRESS / ARC_TESTNET_RPC from .env
  - ABI from contracts/out/

For each of the 6 addresses (3 old gemini/deepseek/qwen + 3 v2 wallets derived
from HACKATHON_WALLET_PRIVATE_KEY), prints:
  - getReputation(addr)        — raw uint256 + scaled float
  - getStats(addr)             — totalBids/totalWins/qualityPasses/cumulativeFees/score
  - reps(addr).lastUpdated     — to distinguish "never touched" from "score reset"
  - auction.registered(addr)   — whether they finished registerAgent
  - auction.stakes(addr) / lockedStakes(addr) — staked USDC
  - reputation_registry.authorized(addr) — only relevant for owner check
  - USDC.balanceOf(addr) and ETH balance

Plus derives v2 wallets from HACKATHON_WALLET_PRIVATE_KEY and confirms the
addresses match outputs/agent_wallets.json (rules out hypothesis B).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Load .env manually (avoid pulling in app deps)
ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def load_env() -> None:
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env()
sys.path.insert(0, str(ROOT))

from web3 import Web3  # noqa: E402
from eth_account import Account  # noqa: E402

from polyglot_alpha.agents.wallets import derive_agent_wallet, AGENT_NAMES  # noqa: E402


def load_abi(path: Path) -> list:
    return json.loads(path.read_text())["abi"]


# ERC-20 minimal ABI for balanceOf
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def main() -> int:
    rpc = os.environ["ARC_TESTNET_RPC"]
    rep_addr = os.environ["REPUTATION_REGISTRY_ADDRESS"]
    auc_addr = os.environ["TRANSLATION_AUCTION_ADDRESS"]
    usdc_addr = os.environ["ARC_TESTNET_USDC_ADDRESS"]
    op_pk = os.environ["HACKATHON_WALLET_PRIVATE_KEY"]

    print(f"RPC                  = {rpc}")
    print(f"ReputationRegistry   = {rep_addr}")
    print(f"TranslationAuction   = {auc_addr}")
    print(f"USDC                 = {usdc_addr}")

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    print(f"chain connected      = {w3.is_connected()}  chainId={w3.eth.chain_id}")

    rep_abi = load_abi(ROOT / "contracts/out/ReputationRegistry.sol/ReputationRegistry.json")
    auc_abi = load_abi(ROOT / "contracts/out/TranslationAuction.sol/TranslationAuction.json")

    rep = w3.eth.contract(address=Web3.to_checksum_address(rep_addr), abi=rep_abi)
    auc = w3.eth.contract(address=Web3.to_checksum_address(auc_addr), abi=auc_abi)
    usdc = w3.eth.contract(address=Web3.to_checksum_address(usdc_addr), abi=ERC20_ABI)

    # Confirm wallet derivation
    print("\n=== Wallet derivation cross-check (rules out hypothesis B) ===")
    derived: dict[str, str] = {}
    for name in AGENT_NAMES:
        w = derive_agent_wallet(op_pk, name)
        derived[name] = w.address
        print(f"  {name:<14} -> {w.address}")
    saved = json.loads((ROOT / "outputs/agent_wallets.json").read_text())
    for name in AGENT_NAMES:
        match = derived[name].lower() == saved[name]["address"].lower()
        print(
            f"  {name:<14} saved={saved[name]['address']} match={match}"
        )

    # 6 addresses to investigate. Old gemini/deepseek/qwen addresses
    # come from the original W14-CONTRACT-PREP keys (sha256(op_pk + ':gemini') etc).
    # If that derivation has been retired we still want to inspect the
    # historic addresses cited in the bug report.
    old_addresses = {
        "gemini-old":   "0x396B...",  # placeholder; only the v2 row is required
    }
    # We don't actually know the literal old hex from the env; derive what the
    # PRE-v2 naming would have produced (sha256(pk + ':gemini')).
    import hashlib
    for legacy_name in ("gemini", "deepseek", "qwen"):
        seed = hashlib.sha256(f"{op_pk}:{legacy_name}".encode()).hexdigest()
        acct = Account.from_key("0x" + seed)
        old_addresses[f"{legacy_name}-old"] = acct.address

    targets: list[tuple[str, str]] = []
    for legacy in ("gemini", "deepseek", "qwen"):
        targets.append((f"{legacy}-old", old_addresses[f"{legacy}-old"]))
    for name in AGENT_NAMES:
        targets.append((name, derived[name]))

    print("\n=== Per-address chain state ===")
    print(
        f"{'name':<15}{'address':<45}{'rep_raw':>22}{'rep_f':>10}"
        f"{'lastUpd':>12}{'tBids':>7}{'tWins':>7}{'qPass':>7}"
        f"{'cumFee':>10}{'reg':>5}{'stake':>10}{'lock':>10}"
        f"{'usdc':>10}{'eth':>10}"
    )
    for label, addr in targets:
        addr_cs = Web3.to_checksum_address(addr)
        rep_raw = rep.functions.getReputation(addr_cs).call()
        stats = rep.functions.getStats(addr_cs).call()
        reps_struct = rep.functions.reps(addr_cs).call()
        # reps struct: totalBids, totalWins, totalQualityPasses, cumulativeFeesEarned, score, lastUpdated
        last_updated = reps_struct[5]
        registered = auc.functions.registered(addr_cs).call()
        stake = auc.functions.stakes(addr_cs).call()
        try:
            locked = auc.functions.lockedStakes(addr_cs).call()
        except Exception:
            locked = 0
        usdc_bal = usdc.functions.balanceOf(addr_cs).call()
        eth_bal = w3.eth.get_balance(addr_cs)
        print(
            f"{label:<15}{addr_cs:<45}{rep_raw:>22}{rep_raw/1e18:>10.4f}"
            f"{last_updated:>12}{stats[0]:>7}{stats[1]:>7}{stats[2]:>7}"
            f"{stats[3]:>10}{int(registered):>5}{stake/1e6:>10.4f}{locked/1e6:>10.4f}"
            f"{usdc_bal/1e6:>10.4f}{eth_bal/1e18:>10.4f}"
        )

    # Owner of ReputationRegistry + authorized set
    try:
        owner = rep.functions.owner().call()
        print(f"\nReputationRegistry.owner = {owner}")
    except Exception as exc:
        print(f"\nReputationRegistry.owner read failed: {exc}")

    # Also dump bytecode size to confirm both contracts are deployed
    rep_code = w3.eth.get_code(Web3.to_checksum_address(rep_addr))
    auc_code = w3.eth.get_code(Web3.to_checksum_address(auc_addr))
    print(
        f"\nReputationRegistry bytecode bytes = {len(rep_code)}"
        f"  TranslationAuction bytecode bytes = {len(auc_code)}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
