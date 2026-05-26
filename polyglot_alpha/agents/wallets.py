"""Deterministic agent wallet derivation.

Each reference seeder slot (``gemini``, ``deepseek``, ``qwen``) gets a
fresh keypair derived from the operator private key + the slot name.
The slot names are kept as the pre-rename identifiers so historical
on-chain reputation and persisted bid records remain stable; the
surfaced agent personas are :class:`SeederAlpha` / :class:`SeederBeta`
/ :class:`SeederGamma`. This gives us wallets that are:

* Stable across process restarts (no PK to store on disk).
* Disjoint from the operator wallet (so an agent compromise can not
  drain the operator's USDC/ETH).
* Re-derivable on any host that has the operator PK.

Derivation::

    raw_seed = sha256(operator_pk_hex + ":" + agent_name)
    private_key = "0x" + raw_seed.hexdigest()

We also persist the *public* addresses (no PKs) to
``outputs/agent_wallets.json`` so external tooling (faucet scripts,
explorer dashboards) can discover them without re-running derivation.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from eth_account import Account
from eth_account.signers.local import LocalAccount

_REPO_ROOT = Path(__file__).resolve().parents[2]
WALLETS_PATH = _REPO_ROOT / "outputs" / "agent_wallets.json"

AGENT_NAMES: tuple[str, ...] = ("gemini", "deepseek", "qwen")


@dataclass(frozen=True)
class AgentWallet:
    name: str
    address: str
    private_key: str
    env_var: str


def _env_var(name: str) -> str:
    return f"{name.upper()}_WALLET_PRIVATE_KEY"


def derive_agent_private_key(operator_pk: str, agent_name: str) -> str:
    """Return a deterministic 0x-prefixed 32-byte hex private key."""

    if not operator_pk:
        raise ValueError("operator_pk is required for derivation")
    if not agent_name:
        raise ValueError("agent_name is required for derivation")
    seed = hashlib.sha256(f"{operator_pk}:{agent_name}".encode()).hexdigest()
    return "0x" + seed


def derive_agent_wallet(operator_pk: str, agent_name: str) -> AgentWallet:
    """Derive one agent's wallet without touching disk."""

    pk = derive_agent_private_key(operator_pk, agent_name)
    acct: LocalAccount = Account.from_key(pk)
    return AgentWallet(
        name=agent_name,
        address=acct.address,
        private_key=pk,
        env_var=_env_var(agent_name),
    )


def derive_all_wallets(
    operator_pk: Optional[str] = None,
) -> dict[str, AgentWallet]:
    """Derive wallets for all three reference seeder slots."""

    pk = operator_pk or os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
    if not pk:
        raise RuntimeError(
            "HACKATHON_WALLET_PRIVATE_KEY not set; cannot derive agent wallets"
        )
    return {name: derive_agent_wallet(pk, name) for name in AGENT_NAMES}


def persist_public_addresses(
    wallets: dict[str, AgentWallet],
    *,
    path: Optional[Path] = None,
) -> Path:
    """Write public addresses (no PKs) to ``outputs/agent_wallets.json``.

    Idempotent — overwrites the file with the same content if the
    addresses have not changed.
    """

    target = path or WALLETS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        name: {"address": w.address, "env_var": w.env_var}
        for name, w in wallets.items()
    }
    target.write_text(json.dumps(payload, indent=2) + "\n")
    return target


def load_or_derive_wallet(agent_name: str) -> AgentWallet:
    """Prefer an explicit env-var PK if set; otherwise derive."""

    explicit = os.environ.get(_env_var(agent_name))
    if explicit:
        acct = Account.from_key(explicit)
        return AgentWallet(
            name=agent_name,
            address=acct.address,
            private_key=explicit,
            env_var=_env_var(agent_name),
        )
    op_pk = os.environ.get("HACKATHON_WALLET_PRIVATE_KEY")
    if not op_pk:
        raise RuntimeError(
            f"neither {_env_var(agent_name)} nor HACKATHON_WALLET_PRIVATE_KEY"
            f" is set; cannot resolve {agent_name} wallet"
        )
    return derive_agent_wallet(op_pk, agent_name)


__all__ = [
    "AgentWallet",
    "AGENT_NAMES",
    "WALLETS_PATH",
    "derive_agent_private_key",
    "derive_agent_wallet",
    "derive_all_wallets",
    "load_or_derive_wallet",
    "persist_public_addresses",
]
