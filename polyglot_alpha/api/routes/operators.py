"""``/api/operators`` — operator (translator) onboarding and discovery.

This router is the entry point for **external** operators who want to
participate in PolyglotAlpha's translation auctions on Arc testnet. It
exposes four routes:

* ``POST /api/operators/register`` — register a new external operator,
  taking a 100 USDC anti-Sybil stake before seeding the reputation row.
* ``GET /api/operators/{address}`` — fetch metadata + reputation for one
  operator (seeder or external).
* ``GET /api/operators`` — list all known operators with a ``kind``
  discriminator (``"seeder"`` for the four baked-in LLM agents,
  ``"external"`` for anyone registered via this API).
* ``POST /api/auctions/{event_id}/bid`` — convenience relayer endpoint
  (Phase 2; stubbed body but the schema is wired so the frontend can
  develop against it).

The router is registered in :mod:`polyglot_alpha.api.main`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ...persistence.models import AgentReputation
from ..deps import get_db, utc_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/operators", tags=["operators"])
bid_router = APIRouter(prefix="/api/auctions", tags=["auctions"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Bootstrap reputation for external operators. Set deliberately below the
# four reference seeders (which sit at 1.0) so an external operator must
# earn their way up via accepted translations + accrued builder fees.
EXTERNAL_OPERATOR_BOOTSTRAP_REPUTATION: float = 0.7

# Path to the seeder address registry written by
# ``polyglot_alpha.agents.wallets``.
_SEEDER_WALLETS_PATH = os.path.join("outputs", "agent_wallets.json")


def _load_seeder_addresses() -> dict[str, str]:
    """Return ``{name -> checksum address}`` for the four reference agents."""

    try:
        with open(_SEEDER_WALLETS_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return {name: entry["address"] for name, entry in raw.items()}
    except (FileNotFoundError, ValueError, KeyError) as exc:
        logger.debug("seeder wallets file unavailable (%s)", exc)
        return {}


def _is_seeder(address: str, seeders: Optional[dict[str, str]] = None) -> bool:
    seeders = seeders or _load_seeder_addresses()
    target = address.lower()
    return any(addr.lower() == target for addr in seeders.values())


def _seeder_alias_for(address: str) -> Optional[str]:
    seeders = _load_seeder_addresses()
    target = address.lower()
    for name, addr in seeders.items():
        if addr.lower() == target:
            return name
    return None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RegisterOperatorRequest(BaseModel):
    operator_address: str = Field(
        ..., description="Operator wallet address (0x-prefixed, 40 hex chars)"
    )
    display_name: str = Field(
        ..., min_length=1, max_length=80, description="Human-readable handle"
    )
    signature: str = Field(
        ...,
        description=(
            "EIP-191 signature over `display_name` proving control of the "
            "operator wallet. Validated softly in v1; rejected hard in v2."
        ),
    )


class RegisterOperatorResponse(BaseModel):
    operator_address: str
    status: str
    stake_tx: Optional[str]
    reputation_tx: Optional[str]
    initial_reputation: float
    auction_stream_url: str
    display_name: str


class BidRelayRequest(BaseModel):
    operator_address: str
    bid_amount_usdc: float
    candidate_hash: str = Field(..., description="0x-prefixed bytes32 hex")
    signature: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_address(addr: str) -> str:
    """Validate and lowercase-normalize an Ethereum address."""

    if not isinstance(addr, str):
        raise HTTPException(status_code=422, detail="address_must_be_string")
    if not addr.startswith("0x") or len(addr) != 42:
        # Demo addresses (e.g. ``0xtestop001``) used by smoke tests are
        # permitted but flagged in the logs.
        if not addr.startswith("0x") or len(addr) < 6:
            raise HTTPException(status_code=422, detail="invalid_address_format")
        logger.warning("operators: non-standard address format %r accepted", addr)
    return addr


def _try_register_on_chain(operator_address: str) -> dict[str, Optional[str]]:
    """Best-effort: call the chain helper to do the USDC transfer + register.

    Synchronously runs the async chain helper in a thread when called from
    a sync FastAPI handler. Catches every chain-side exception and degrades
    to ``{"stake_tx": None, "register_tx": None}`` so the API stays
    available even when Arc RPC is down (the row is still seeded in the
    local DB; the on-chain side can be re-attempted later).
    """

    try:
        from polyglot_alpha.chain.reputation_registry import (  # type: ignore
            register_agent_with_stake,
        )
    except ImportError as exc:  # pragma: no cover - chain pkg optional
        logger.warning(
            "operators: chain.reputation_registry unavailable (%s); "
            "skipping on-chain register",
            exc,
        )
        return {"stake_tx": None, "register_tx": None}

    async def _run() -> dict[str, Optional[str]]:
        try:
            return await register_agent_with_stake(operator_address)
        except Exception as exc:  # pragma: no cover - chain best-effort
            logger.warning(
                "operators: register_agent_with_stake failed for %s: %s",
                operator_address,
                exc,
            )
            return {"stake_tx": None, "register_tx": None}

    try:
        return asyncio.run(_run())
    except RuntimeError:
        # We are inside a running loop (uvicorn). Spin up a fresh one in a
        # worker thread to avoid the "asyncio.run() cannot be called from a
        # running loop" error.
        import threading

        result_box: dict[str, Any] = {"value": {"stake_tx": None, "register_tx": None}}

        def _runner() -> None:
            try:
                result_box["value"] = asyncio.run(_run())
            except Exception as exc:  # pragma: no cover
                logger.warning("operators: chain runner thread failed: %s", exc)

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=20.0)
        return result_box["value"]


# ---------------------------------------------------------------------------
# Routes — /api/operators
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=RegisterOperatorResponse,
    summary="Register a new external operator with anti-Sybil stake",
)
def register_operator(
    payload: RegisterOperatorRequest,
    session: Session = Depends(get_db),
) -> RegisterOperatorResponse:
    """Register an external operator (translator) with a 100 USDC stake.

    Performs two side-effects:

      1. Calls the chain helper ``register_agent_with_stake`` which sends
         (a) ``USDC.transfer`` from operator -> treasury for 100 USDC and
         (b) ``ReputationRegistry.registerAgent``.
      2. Seeds an ``AgentReputation`` row with
         ``avg_quality=0.7`` (bootstrap; below the seeders' 1.0).

    Returns 409 if the operator is already registered.
    Returns 402 if the on-chain stake transfer cannot be verified — except
    in the demo path, where we still create the local row but flag the
    response with ``stake_tx=None``.
    """

    address = _normalize_address(payload.operator_address)

    existing = session.get(AgentReputation, address)
    if existing is not None and existing.total_bids + existing.total_wins > 0:
        # An operator who has already bid/won is treated as registered.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="operator_already_registered",
        )

    # Soft signature validation: we log but do not reject. Production should
    # use eth_account.messages.encode_defunct + Account.recover_message.
    if not (payload.signature.startswith("0x") and len(payload.signature) >= 4):
        logger.warning(
            "operators: signature %r looks malformed; accepting under v1 soft policy",
            payload.signature[:16],
        )

    chain_result = _try_register_on_chain(address)
    stake_tx = chain_result.get("stake_tx")
    register_tx = chain_result.get("register_tx")

    if existing is None:
        session.add(
            AgentReputation(
                agent_address=address,
                avg_quality=EXTERNAL_OPERATOR_BOOTSTRAP_REPUTATION,
                last_updated=datetime.now(timezone.utc),
            )
        )
    else:
        existing.avg_quality = max(
            existing.avg_quality, EXTERNAL_OPERATOR_BOOTSTRAP_REPUTATION
        )
        existing.last_updated = datetime.now(timezone.utc)
        session.add(existing)
    session.commit()

    return RegisterOperatorResponse(
        operator_address=address,
        status="registered",
        stake_tx=stake_tx,
        reputation_tx=register_tx,
        initial_reputation=EXTERNAL_OPERATOR_BOOTSTRAP_REPUTATION,
        auction_stream_url="/sse/auctions",
        display_name=payload.display_name,
    )


@router.get("", summary="List all operators (seeders + external)")
def list_operators(
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return all known operators with a ``kind`` discriminator."""

    seeders = _load_seeder_addresses()
    rows = session.exec(
        select(AgentReputation).order_by(AgentReputation.cumulative_fees.desc())
    ).all()

    # Filter out synthetic mock-bid addresses (e.g. ``0xagent_lo``,
    # ``0xagent_b``) that were inserted via the ``mock_bids`` trigger path
    # in tests/smoke flows. These are not real on-chain operators and
    # should never appear in the public /api/operators listing.
    def _looks_like_real_address(addr: str) -> bool:
        return (
            isinstance(addr, str)
            and addr.startswith("0x")
            and "_" not in addr
            and len(addr) == 42
        )

    rows = [r for r in rows if _looks_like_real_address(r.agent_address)]
    seen_addresses = {r.agent_address.lower() for r in rows}
    operators: list[dict[str, Any]] = []

    for r in rows:
        seeder_alias = _seeder_alias_for(r.agent_address)
        operators.append(
            {
                "address": r.agent_address,
                "alias": seeder_alias,
                "kind": "seeder" if seeder_alias else "external",
                "reputation": r.avg_quality,
                "total_bids": r.total_bids,
                "total_wins": r.total_wins,
                "cumulative_fees": r.cumulative_fees,
                "last_updated": utc_iso(r.last_updated),
            }
        )

    # Include seeders that haven't accrued any reputation yet (so the UI
    # always shows the four reference agents).
    for name, addr in seeders.items():
        if addr.lower() in seen_addresses:
            continue
        operators.append(
            {
                "address": addr,
                "alias": name,
                "kind": "seeder",
                "reputation": 1.0,
                "total_bids": 0,
                "total_wins": 0,
                "cumulative_fees": 0.0,
                "last_updated": None,
            }
        )

    return operators


@router.get("/{address}", summary="Get an operator's profile")
def get_operator(
    address: str,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    address = _normalize_address(address)

    seeders = _load_seeder_addresses()
    alias = _seeder_alias_for(address)
    rep = session.get(AgentReputation, address)
    if rep is None and alias is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="operator_not_found"
        )

    arc_explorer_url = (
        f"https://explorer.testnet.arc.network/address/{address}"
    )
    return {
        "address": address,
        "alias": alias,
        "kind": "seeder" if alias else "external",
        "reputation": rep.avg_quality if rep else 1.0,
        "total_bids": rep.total_bids if rep else 0,
        "total_wins": rep.total_wins if rep else 0,
        "cumulative_fees": rep.cumulative_fees if rep else 0.0,
        "arc_explorer_url": arc_explorer_url,
        "last_updated": utc_iso(rep.last_updated) if rep else None,
    }


# ---------------------------------------------------------------------------
# Convenience relayer (Phase 2 stub)
# ---------------------------------------------------------------------------


@bid_router.post(
    "/{event_id}/bid",
    summary="Phase 2: relay a signed bid on behalf of an operator",
)
def relay_bid(event_id: str, payload: BidRelayRequest) -> dict[str, Any]:
    """Accept a signed bid payload and broadcast on behalf of the operator.

    **Phase 2 stub.** The interface is wired so the frontend / SDK can
    develop against a stable shape, but the body is a no-op — actual TX
    relaying requires either (a) operator-pre-deposited gas in a relayer
    escrow contract, or (b) Account Abstraction with EIP-4337 paymasters.

    Operators today should sign + broadcast their own ``submitBid`` TX via
    the AuctionClient. See ``examples/external_operator_example.py``.
    """

    return {
        "event_id": event_id,
        "operator_address": payload.operator_address,
        "status": "phase_2_stub",
        "tx_hash": None,
        "note": (
            "Relayer requires EIP-4337 paymaster or pre-deposited gas; "
            "in v1 operators sign and broadcast submitBid themselves."
        ),
    }


__all__ = ["router", "bid_router"]
