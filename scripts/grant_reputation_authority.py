"""One-shot: grant the operator wallet ``authorized`` on ReputationRegistry.

The deployed ``ReputationRegistry`` contract gates ``updateOnAuction``,
``updateOnQuality``, ``updateOnFee``, and ``slashReputation`` behind an
``onlyAuthorized`` modifier. The contract owner is implicitly authorized
(``msg.sender == owner`` is accepted), and the constructor seeds
``authorized[owner] = true``, so when the operator wallet IS the owner
this script is a no-op and exits 0 without sending a tx.

Usage::

    python scripts/grant_reputation_authority.py            # operator self
    python scripts/grant_reputation_authority.py 0xDeadBeef # explicit grantee

The signer is always the operator wallet (``HACKATHON_WALLET_PRIVATE_KEY``);
the optional positional arg is the *grantee* address. Only the contract
owner can call ``setAuthorized``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Make ``polyglot_alpha`` importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - optional
    pass

from web3 import Web3

from polyglot_alpha.chain.reputation_registry import (
    is_authorized,
    set_authorized,
)
from polyglot_alpha.onchain import OnChainClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("grant_reputation_authority")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "grantee",
        nargs="?",
        default=None,
        help=(
            "Address to grant 'authorized' to. Defaults to "
            "HACKATHON_WALLET_ADDRESS (the operator wallet)."
        ),
    )
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="Revoke instead of grant (calls setAuthorized(addr, false)).",
    )
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()

    operator_addr = os.environ.get("HACKATHON_WALLET_ADDRESS")
    if not operator_addr:
        logger.error("HACKATHON_WALLET_ADDRESS not set")
        return 2

    grantee = args.grantee or operator_addr
    grantee = Web3.to_checksum_address(grantee)
    operator_addr = Web3.to_checksum_address(operator_addr)

    client = OnChainClient()
    owner = Web3.to_checksum_address(
        client.reputation.functions.owner().call()
    )
    logger.info(
        "ReputationRegistry @ %s — owner=%s, operator=%s, grantee=%s",
        client.config.reputation_registry,
        owner,
        operator_addr,
        grantee,
    )

    if owner != operator_addr:
        logger.error(
            "operator wallet is NOT the contract owner; setAuthorized "
            "would revert. Owner=%s, operator=%s",
            owner,
            operator_addr,
        )
        return 3

    already = await is_authorized(grantee)
    if already and not args.revoke:
        logger.info(
            "grantee %s is already authorized (owner-implicit or explicit); "
            "skipping setAuthorized",
            grantee,
        )
        return 0
    if (not already) and args.revoke:
        logger.info(
            "grantee %s is not currently authorized; nothing to revoke",
            grantee,
        )
        return 0

    desired = not args.revoke
    logger.info(
        "sending setAuthorized(%s, %s)…", grantee, desired
    )
    tx_hash = await set_authorized(grantee, desired)
    logger.info("setAuthorized tx=%s", tx_hash)

    # Confirm new state.
    new_state = await is_authorized(grantee)
    logger.info(
        "post-tx authorized[%s] = %s (expected %s)",
        grantee,
        new_state,
        desired,
    )
    if bool(new_state) != bool(desired):
        logger.error("authorization state did not flip — check tx receipt")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
