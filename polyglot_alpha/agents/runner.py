"""Standalone runner for the four translator agents.

Two modes:

* ``--agent gemini|deepseek|qwen|llama``: run a single agent process
  (intended for ``python -m polyglot_alpha.agents.runner --agent=gemini``).
* ``--all``: spin up all four agents as concurrent asyncio tasks in one
  process (handy for local end-to-end demos).

Wallets are loaded from per-agent env vars; ``--bootstrap-wallets`` will
generate four fresh wallets, persist their public addresses to
``outputs/agent_wallets.json``, and **print** the private keys to stdout
so the operator can stash them in env vars themselves. Private keys are
never written to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from eth_account import Account

from . import AGENT_REGISTRY, BaseTranslatorAgent

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WALLETS_PATH = _REPO_ROOT / "outputs" / "agent_wallets.json"


def _agent_env_var(name: str) -> str:
    return f"{name.upper()}_WALLET_PRIVATE_KEY"


def bootstrap_wallets(*, write_to: Optional[Path] = None) -> dict[str, dict[str, str]]:
    """Generate four fresh test wallets.

    Public addresses are persisted to ``outputs/agent_wallets.json`` (or
    ``write_to`` if provided). Private keys are returned in-memory only;
    the caller is responsible for placing them in per-agent env vars.
    """

    target = write_to or _WALLETS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict[str, str]] = {}
    public_payload: dict[str, dict[str, str]] = {}
    for name in AGENT_REGISTRY:
        acct = Account.create()
        # ``acct.key`` is HexBytes in newer eth_account; ``.hex()`` may omit
        # the ``0x`` prefix on some versions. Normalize so downstream consumers
        # always see ``0x...`` (which is what eth_account.Account.from_key
        # accepts unambiguously).
        raw_hex = acct.key.hex()
        if not raw_hex.startswith("0x"):
            raw_hex = "0x" + raw_hex
        result[name] = {
            "address": acct.address,
            "private_key": raw_hex,
            "env_var": _agent_env_var(name),
        }
        public_payload[name] = {
            "address": acct.address,
            "env_var": _agent_env_var(name),
        }
    target.write_text(json.dumps(public_payload, indent=2))
    return result


async def _run_one(name: str) -> None:
    cls = AGENT_REGISTRY[name]
    pk = os.environ.get(_agent_env_var(name))
    if not pk:
        logger.error(
            "agent=%s missing env var %s; skip", name, _agent_env_var(name)
        )
        return
    agent: BaseTranslatorAgent = cls(wallet_pk=pk)
    logger.info(
        "agent=%s address=%s model=%s starting listener",
        name,
        agent.address,
        agent.MODEL_ID,
    )
    await agent.ensure_registered()
    await agent.listen_for_events()


async def _run_all() -> None:
    tasks = [asyncio.create_task(_run_one(name)) for name in AGENT_REGISTRY]
    await asyncio.gather(*tasks)


def _print_bootstrap_table(wallets: dict[str, dict[str, str]]) -> None:
    print("\n=== Translator agent wallets ===\n", file=sys.stderr)
    print(f"Public addresses written to {_WALLETS_PATH}", file=sys.stderr)
    print(
        "\nPaste the lines below into your shell / .env (private keys are not"
        " written to disk):\n",
        file=sys.stderr,
    )
    for name, info in wallets.items():
        print(f"export {info['env_var']}={info['private_key']}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent",
        choices=sorted(AGENT_REGISTRY),
        help="Run a single agent process.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all four agents as concurrent tasks in this process.",
    )
    parser.add_argument(
        "--bootstrap-wallets",
        action="store_true",
        help="Generate four fresh wallets, write public addresses to outputs/, print keys.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.bootstrap_wallets:
        wallets = bootstrap_wallets()
        _print_bootstrap_table(wallets)
        return 0

    if args.all:
        asyncio.run(_run_all())
        return 0

    if not args.agent:
        parser.error("provide --agent NAME, --all, or --bootstrap-wallets")
    asyncio.run(_run_one(args.agent))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
