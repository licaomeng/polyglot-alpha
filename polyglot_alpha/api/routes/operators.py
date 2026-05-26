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
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ...persistence.models import AgentReputation, BuilderFeeEvent
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

# Default anti-Sybil stake (USDC) for operator registration. Mirrors
# ``polyglot_alpha.chain.reputation_registry.ANTI_SYBIL_STAKE_USDC`` but is
# duplicated here to avoid importing the optional chain module in the API
# layer (the chain module pulls in web3, which may not be installed in the
# minimal API deployment).
DEFAULT_ANTI_SYBIL_STAKE_USDC: float = 100.0

# Auction contract ``REGISTRATION_STAKE`` mirrored as a float (5 USDC). This
# is the bidding stake locked inside ``TranslationAuction`` — the same value
# returned by ``stakes(agent)`` for a freshly registered agent. We keep it
# as a constant here so the API can describe mock-mode stake balances
# without importing the chain module.
DEFAULT_AGENT_STAKE_USDC: float = 5.0

# In-process mock-mode stake ledger. Keyed by ``address.lower()``; tracks
# the simulated stake balance and (optional) ``locked_until_block`` so the
# UI can demo the lock/withdraw cycle without touching Arc RPC. Live-mode
# requests bypass this dict entirely and consult the chain client instead.
_MOCK_STAKE_LEDGER: dict[str, dict[str, Any]] = {}

# Path to the seeder address registry written by
# ``polyglot_alpha.agents.wallets``.
_SEEDER_WALLETS_PATH = os.path.join("outputs", "agent_wallets.json")

# Supported language codes for the operator multi-select. Mirrors the
# language codes the orchestrator already routes through Haiku translators.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("zh", "ru", "es", "ja", "ar", "en")


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
    signature: Optional[str] = Field(
        default=None,
        description=(
            "EIP-191 signature over `display_name` proving control of the "
            "operator wallet. Validated softly in v1; rejected hard in v2."
        ),
    )
    model_label: Optional[str] = Field(
        default=None,
        max_length=120,
        description=(
            "Free-text model descriptor (e.g. 'claude-opus-4-7', "
            "'gpt-4o + RAG'). Stored as metadata only; not validated."
        ),
    )
    languages: Optional[list[str]] = Field(
        default=None,
        description=(
            "Supported language codes (subset of SUPPORTED_LANGUAGES). "
            "Used for routing matching auctions to the operator."
        ),
    )
    stake_amount_usdc: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Anti-Sybil stake in USDC. Defaults to "
            f"{DEFAULT_ANTI_SYBIL_STAKE_USDC} USDC if omitted."
        ),
    )
    mode: Optional[Literal["live", "mock"]] = Field(
        default="live",
        description=(
            "When 'mock', skip real chain RPC and return simulated tx "
            "hashes. The DB row is still seeded so the UI can render the "
            "registration. Used for the no-gas demo path."
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
    registration_id: Optional[str] = None
    is_simulated: bool = False
    success: bool = True


class ClaimFeesRequest(BaseModel):
    mode: Optional[Literal["live", "mock"]] = Field(
        default="live",
        description=(
            "When 'mock', skip real chain RPC, mark accrued rows as "
            "claimed in the local DB, and return a synthetic tx hash."
        ),
    )


class ClaimFeesResponse(BaseModel):
    success: bool
    tx_hash: Optional[str]
    amount_claimed_usdc: float
    is_simulated: bool
    operator_address: str


class PendingFeesResponse(BaseModel):
    operator_address: str
    pending_usdc: float
    event_count: int


class WithdrawStakeRequest(BaseModel):
    mode: Optional[Literal["live", "mock"]] = Field(
        default="live",
        description=(
            "When 'mock', skip real chain RPC, reset the in-memory stake "
            "ledger entry, and return a synthetic 0xsim_ tx hash."
        ),
    )
    private_key: Optional[str] = Field(
        default=None,
        description=(
            "Operator private key (0x-prefixed). REQUIRED in live mode to "
            "sign the ``withdrawStake()`` transaction. Ignored in mock mode."
        ),
    )


class WithdrawStakeResponse(BaseModel):
    success: bool
    tx_hash: Optional[str]
    amount_recovered_usdc: float
    is_simulated: bool
    operator_address: str


class StakeStatusResponse(BaseModel):
    operator_address: str
    staked: bool
    amount_usdc: float
    locked_until_block: Optional[int] = None
    can_withdraw: bool


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


def _try_register_on_chain(
    operator_address: str,
    *,
    stake_usdc: float = DEFAULT_ANTI_SYBIL_STAKE_USDC,
) -> dict[str, Optional[str]]:
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
            return await register_agent_with_stake(
                operator_address, stake_usdc=stake_usdc
            )
        except Exception as exc:  # pragma: no cover - chain best-effort
            logger.warning(
                "operators: register_agent_with_stake failed for %s: %s",
                operator_address,
                exc,
            )
            return {"stake_tx": None, "register_tx": None}

    return _run_async_chain_call(_run, fallback={"stake_tx": None, "register_tx": None})


def _try_claim_fees_on_chain(operator_address: str) -> Optional[str]:
    """Best-effort: call ``builder_fee_router.claim_fees`` for the operator.

    Returns the tx hash on success, ``None`` on any chain-side failure.
    """

    try:
        from polyglot_alpha.chain.builder_fee_router import (  # type: ignore
            claim_fees,
        )
    except ImportError as exc:  # pragma: no cover - chain pkg optional
        logger.warning(
            "operators: chain.builder_fee_router unavailable (%s); "
            "skipping on-chain claim",
            exc,
        )
        return None

    async def _run() -> Optional[str]:
        try:
            return await claim_fees(operator_address)
        except Exception as exc:  # pragma: no cover - chain best-effort
            logger.warning(
                "operators: claim_fees failed for %s: %s",
                operator_address,
                exc,
            )
            return None

    return _run_async_chain_call(_run, fallback=None)


def _try_withdraw_stake_on_chain(
    operator_address: str, private_key: str
) -> dict[str, Any]:
    """Best-effort: call ``AuctionClient.withdraw_stake`` for the operator.

    Returns a dict with keys ``tx_hash`` (or ``None`` on failure),
    ``amount_recovered_usdc`` (the unlocked stake recovered), and ``error``
    (a short code from ``{locked, no_stake, rpc_timeout, ...}`` when the
    chain side reverted). The contract ``withdrawStake()`` reverts with
    ``"slashable window open"`` while the 72h lock is active and ``"no
    unlocked stake"`` when nothing is withdrawable; we map both to
    structured codes here so the route can translate them into 409 / 404.
    """

    try:
        from polyglot_alpha.chain.auction_client import (  # type: ignore
            AuctionClient,
        )
        from polyglot_alpha.onchain import units_to_usdc  # type: ignore
    except ImportError as exc:  # pragma: no cover - chain pkg optional
        logger.warning(
            "operators: chain.auction_client unavailable (%s); "
            "skipping on-chain withdraw",
            exc,
        )
        return {
            "tx_hash": None,
            "amount_recovered_usdc": 0.0,
            "error": "chain_unavailable",
        }

    async def _run() -> dict[str, Any]:
        try:
            client = AuctionClient()
            # Peek the current unlocked balance so we can return an
            # accurate ``amount_recovered_usdc`` even though
            # ``withdrawStake()`` itself does not return a value.
            checksum = client.onchain.w3.to_checksum_address(operator_address)
            stakes_units = int(
                client.onchain.auction.functions.stakes(checksum).call()
            )
            locked_units = int(
                client.onchain.auction.functions.lockedStakes(checksum).call()
            )
            unlocked_units = max(stakes_units - locked_units, 0)
            tx_hash = await client.withdraw_stake(private_key)
            return {
                "tx_hash": tx_hash,
                "amount_recovered_usdc": units_to_usdc(unlocked_units),
                "error": None,
            }
        except Exception as exc:  # pragma: no cover - chain best-effort
            message = str(exc).lower()
            error_code = "rpc_error"
            if "slashable" in message or "lock" in message:
                error_code = "locked"
            elif "no unlocked" in message or "no stake" in message:
                error_code = "no_stake"
            elif "timeout" in message or "timed out" in message:
                error_code = "rpc_timeout"
            logger.warning(
                "operators: withdraw_stake failed for %s: %s (code=%s)",
                operator_address,
                exc,
                error_code,
            )
            return {
                "tx_hash": None,
                "amount_recovered_usdc": 0.0,
                "error": error_code,
            }

    return _run_async_chain_call(
        _run,
        fallback={
            "tx_hash": None,
            "amount_recovered_usdc": 0.0,
            "error": "rpc_timeout",
        },
    )


def _try_get_stake_status_on_chain(operator_address: str) -> dict[str, Any]:
    """Best-effort: read ``stakes`` / ``lockedStakes`` / ``stakeUnlockAt``.

    Returns ``{"staked", "amount_usdc", "locked_until_block",
    "can_withdraw", "error"}``. On any chain-side failure the route falls
    back to the DB/mock-derived view (registered = staked at the default
    5 USDC amount, unlocked).
    """

    try:
        from polyglot_alpha.onchain import OnChainClient, units_to_usdc  # type: ignore
    except ImportError as exc:  # pragma: no cover - chain pkg optional
        logger.debug(
            "operators: onchain unavailable for stake status (%s)", exc
        )
        return {
            "staked": None,
            "amount_usdc": None,
            "locked_until_block": None,
            "can_withdraw": None,
            "error": "chain_unavailable",
        }

    try:
        client = OnChainClient()
        checksum = client.w3.to_checksum_address(operator_address)
        stakes_units = int(client.auction.functions.stakes(checksum).call())
        locked_units = int(
            client.auction.functions.lockedStakes(checksum).call()
        )
        unlock_at = int(
            client.auction.functions.stakeUnlockAt(checksum).call()
        )
        latest_block_ts = int(client.w3.eth.get_block("latest").timestamp)
        unlocked_units = max(stakes_units - locked_units, 0)
        is_locked = latest_block_ts < unlock_at
        return {
            "staked": stakes_units > 0,
            "amount_usdc": units_to_usdc(stakes_units),
            # ``stakeUnlockAt`` is a unix timestamp (not a block height);
            # we surface it under ``locked_until_block`` for API-shape
            # parity with the schema the UI consumes. ``None`` means
            # no active lock (or never set).
            "locked_until_block": unlock_at if is_locked else None,
            "can_withdraw": (unlocked_units > 0) and not is_locked,
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - chain best-effort
        logger.debug(
            "operators: stake status chain read failed for %s: %s",
            operator_address,
            exc,
        )
        return {
            "staked": None,
            "amount_usdc": None,
            "locked_until_block": None,
            "can_withdraw": None,
            "error": "rpc_error",
        }


def _run_async_chain_call(coro_factory, *, fallback: Any) -> Any:
    """Run an async chain helper from a sync FastAPI handler safely.

    Handles the "already inside a running loop" case by spawning a worker
    thread. Falls back to ``fallback`` on any failure.
    """

    try:
        return asyncio.run(coro_factory())
    except RuntimeError:
        import threading

        result_box: dict[str, Any] = {"value": fallback}

        def _runner() -> None:
            try:
                result_box["value"] = asyncio.run(coro_factory())
            except Exception as exc:  # pragma: no cover
                logger.warning("operators: chain runner thread failed: %s", exc)

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=20.0)
        return result_box["value"]


def _make_sim_tx_hash(prefix: str = "0xsim_") -> str:
    """Return a synthetic tx hash for mock-mode demos.

    Format: ``0xsim_<8 hex chars>``. Matches the prefix the UI helper
    ``arcscanTxUrl`` and ``isSimTxHash`` already recognise as
    non-clickable.
    """

    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _compute_pending_fees(
    session: Session, operator_address: str
) -> tuple[float, int]:
    """Return claimable USDC + supporting fee-event count for the operator.

    Mirrors the on-chain ``BuilderFeeRouter.getCumulativeFees(translator)``
    semantics: this is the amount that ``claimFees`` will pull on the next
    settle. Stored in ``AgentReputation.cumulative_fees`` which the
    orchestrator increments on every ``builder_fee.accrued`` SSE event and
    we zero out on claim.

    The event count is sourced from ``BuilderFeeEvent`` rows for context
    (so the UI can show "3 events totalling $X.YY") but does not affect
    the pending amount itself.
    """

    rep = session.get(AgentReputation, operator_address)
    pending = float(rep.cumulative_fees) if rep is not None else 0.0
    event_count = len(
        session.exec(
            select(BuilderFeeEvent).where(
                BuilderFeeEvent.translator_address == operator_address
            )
        ).all()
    )
    return pending, event_count


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
    """Register an external operator (translator) with an anti-Sybil stake.

    Performs two side-effects:

      1. Calls the chain helper ``register_agent_with_stake`` which sends
         (a) ``USDC.transfer`` from operator -> treasury for the stake
         amount (default 100 USDC) and (b)
         ``ReputationRegistry.registerAgent``.
      2. Seeds an ``AgentReputation`` row with
         ``avg_quality=0.7`` (bootstrap; below the seeders' 1.0).

    Mock mode (``mode="mock"``) skips the chain call entirely and returns
    a synthetic ``0xsim_…`` tx pair so the UI can render the registration
    without burning real testnet gas.

    Returns 409 if the operator has already bid or won. Re-registering a
    cold address (0 bids, 0 wins) is a no-op success.
    """

    address = _normalize_address(payload.operator_address)
    languages = _validate_languages(payload.languages)
    stake_amount = (
        payload.stake_amount_usdc
        if payload.stake_amount_usdc is not None
        else DEFAULT_ANTI_SYBIL_STAKE_USDC
    )
    is_mock = (payload.mode or "live") == "mock"

    existing = session.get(AgentReputation, address)
    if existing is not None and existing.total_bids + existing.total_wins > 0:
        # An operator who has already bid/won is treated as registered.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="operator_already_registered",
        )

    # Soft signature validation: we log but do not reject. Production should
    # use eth_account.messages.encode_defunct + Account.recover_message.
    sig = payload.signature or ""
    if sig and not (sig.startswith("0x") and len(sig) >= 4):
        logger.warning(
            "operators: signature %r looks malformed; accepting under v1 soft policy",
            sig[:16],
        )

    if is_mock:
        stake_tx: Optional[str] = _make_sim_tx_hash()
        register_tx: Optional[str] = _make_sim_tx_hash()
        # Seed the mock-mode stake ledger with the auction-registration
        # stake (5 USDC, unlocked) so the symmetric ``withdraw-stake``
        # endpoint has something to pay out. The anti-Sybil stake (100
        # USDC) is held by the reputation registry, not by the auction
        # contract, so we deliberately do NOT model it here — the mock
        # withdraw flow returns only the 5 USDC auction stake.
        _MOCK_STAKE_LEDGER[address.lower()] = {
            "amount_usdc": DEFAULT_AGENT_STAKE_USDC,
            "locked_until_block": None,
        }
        logger.info(
            "operators.register MOCK mode: address=%s stake=%.4f sim_stake_tx=%s",
            address,
            stake_amount,
            stake_tx,
        )
    else:
        chain_result = _try_register_on_chain(address, stake_usdc=stake_amount)
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

    registration_id = f"reg_{uuid.uuid4().hex[:12]}"
    logger.info(
        "operators.register: address=%s display=%s model=%s langs=%s "
        "stake=%.4f mode=%s registration_id=%s",
        address,
        payload.display_name,
        payload.model_label,
        languages,
        stake_amount,
        "mock" if is_mock else "live",
        registration_id,
    )

    return RegisterOperatorResponse(
        operator_address=address,
        status="registered",
        stake_tx=stake_tx,
        reputation_tx=register_tx,
        initial_reputation=EXTERNAL_OPERATOR_BOOTSTRAP_REPUTATION,
        auction_stream_url="/sse/auctions",
        display_name=payload.display_name,
        registration_id=registration_id,
        is_simulated=is_mock,
        success=True,
    )


def _validate_languages(languages: Optional[list[str]]) -> list[str]:
    """Validate and normalise a list of language codes.

    Empty / ``None`` inputs return an empty list. Unknown codes raise 422
    so the UI form surfaces a clear error to the operator.
    """

    if not languages:
        return []
    unknown = [c for c in languages if c not in SUPPORTED_LANGUAGES]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unsupported_language_codes:{','.join(unknown)}",
        )
    return list(languages)


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
        if not (
            isinstance(addr, str)
            and addr.startswith("0x")
            and "_" not in addr
            and len(addr) == 42
        ):
            return False
        # Mirror the leaderboard filter — reject common test prefixes that
        # made it onto the chain via stress-test fixtures.
        lower = addr.lower()
        if lower.startswith("0xdead") or lower.startswith("0xagent"):
            return False
        # Reject vanity test fixtures: 4+ consecutive identical leading
        # nibbles after the ``0x`` prefix (e.g. ``0xbbbb…``, ``0xaaaa…``).
        if len(lower) >= 6:
            first_nibble = lower[2]
            if all(lower[i] == first_nibble for i in range(2, 6)):
                return False
        return True

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


@router.get(
    "/{address}/pending-fees",
    response_model=PendingFeesResponse,
    summary="Get an operator's claimable (pending) builder fees",
)
def get_pending_fees(
    address: str,
    session: Session = Depends(get_db),
) -> PendingFeesResponse:
    """Return the operator's claimable USDC + supporting event count.

    Wraps the on-chain ``BuilderFeeRouter.getCumulativeFees(translator)``
    semantics via the local DB mirror (see ``_compute_pending_fees``).
    The UI calls this to decide whether to enable the ``Claim Fees``
    button and to show "Claim Fees ($X.XX)".
    """

    address = _normalize_address(address)
    alias = _seeder_alias_for(address)
    rep = session.get(AgentReputation, address)
    if rep is None and alias is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="operator_not_found"
        )

    pending, event_count = _compute_pending_fees(session, address)
    return PendingFeesResponse(
        operator_address=address,
        pending_usdc=pending,
        event_count=event_count,
    )


@router.post(
    "/{address}/claim-fees",
    response_model=ClaimFeesResponse,
    summary="Claim accumulated builder fees for an operator",
)
def claim_operator_fees(
    address: str,
    payload: Optional[ClaimFeesRequest] = None,
    session: Session = Depends(get_db),
) -> ClaimFeesResponse:
    """Withdraw all pending builder fees for ``address``.

    Two-step settle:

      1. Read the local pending balance (mirrors on-chain getCumulativeFees).
      2. Send the on-chain ``claimFees(translator)`` tx via
         ``polyglot_alpha.chain.builder_fee_router.claim_fees``.
      3. Zero out ``AgentReputation.cumulative_fees`` on success to mirror
         the on-chain reset.

    Mock mode (``mode="mock"`` in body) skips the chain call and returns a
    synthetic ``0xsim_…`` hash. The DB row is still zeroed so the UI can
    reflect the settle without burning real testnet gas.

    Validates that the address is a known operator (registered or seeder)
    and returns ``success=False`` with ``amount_claimed_usdc=0`` when the
    pending balance is zero (no-op claim).
    """

    address = _normalize_address(address)
    alias = _seeder_alias_for(address)
    rep = session.get(AgentReputation, address)
    if rep is None and alias is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="operator_not_found"
        )

    pending, _event_count = _compute_pending_fees(session, address)
    if pending <= 0.0:
        return ClaimFeesResponse(
            success=False,
            tx_hash=None,
            amount_claimed_usdc=0.0,
            is_simulated=False,
            operator_address=address,
        )

    is_mock = (payload.mode if payload else "live") == "mock"

    if is_mock:
        tx_hash: Optional[str] = _make_sim_tx_hash()
        logger.info(
            "operators.claim_fees MOCK: address=%s pending=%.6f sim_tx=%s",
            address,
            pending,
            tx_hash,
        )
    else:
        tx_hash = _try_claim_fees_on_chain(address)
        if tx_hash is None:
            # Live claim attempted but chain call failed (RPC down, gas, etc).
            # We do NOT zero the local balance — the operator can retry once
            # chain access is restored. Return success=False so the UI can
            # surface the failure cleanly.
            logger.warning(
                "operators.claim_fees: live chain call failed for %s "
                "(pending=%.6f); local balance preserved for retry",
                address,
                pending,
            )
            return ClaimFeesResponse(
                success=False,
                tx_hash=None,
                amount_claimed_usdc=0.0,
                is_simulated=False,
                operator_address=address,
            )

    # Zero out the local mirror of cumulative_fees to reflect the settle.
    # We intentionally do NOT delete the BuilderFeeEvent audit rows — those
    # are an immutable event log and remain visible in /builder_fees for
    # accounting.
    if rep is not None:
        rep.cumulative_fees = 0.0
        rep.last_updated = datetime.now(timezone.utc)
        session.add(rep)
        session.commit()

    return ClaimFeesResponse(
        success=True,
        tx_hash=tx_hash,
        amount_claimed_usdc=pending,
        is_simulated=is_mock,
        operator_address=address,
    )


@router.get(
    "/{address}/stake-status",
    response_model=StakeStatusResponse,
    summary="Get an operator's bidding stake status",
)
def get_stake_status(
    address: str,
    session: Session = Depends(get_db),
) -> StakeStatusResponse:
    """Return the operator's current bidding stake balance + lock state.

    Wraps the on-chain ``TranslationAuction.stakes(agent)`` /
    ``lockedStakes(agent)`` / ``stakeUnlockAt(agent)`` triplet via
    ``_try_get_stake_status_on_chain``. If the chain layer is unavailable
    or returns an error, falls back to the DB-derived view: any registered
    operator (``AgentReputation`` row exists) is treated as having a
    ``DEFAULT_AGENT_STAKE_USDC`` (5 USDC) unlocked stake; seeders and
    unknown addresses surface ``staked=False``.

    The mock-mode stake ledger (``_MOCK_STAKE_LEDGER``) takes precedence
    over both chain reads and the DB fallback when a record exists, so
    the demo flow can simulate a withdrawn / locked stake without
    burning Arc testnet gas.
    """

    address = _normalize_address(address)
    alias = _seeder_alias_for(address)
    rep = session.get(AgentReputation, address)
    if rep is None and alias is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="operator_not_found",
        )

    # Mock-mode ledger wins when present (set by mock register/withdraw).
    ledger_entry = _MOCK_STAKE_LEDGER.get(address.lower())
    if ledger_entry is not None:
        amount_usdc = float(ledger_entry.get("amount_usdc", 0.0))
        locked_until = ledger_entry.get("locked_until_block")
        is_staked = amount_usdc > 0.0
        can_withdraw = is_staked and locked_until is None
        return StakeStatusResponse(
            operator_address=address,
            staked=is_staked,
            amount_usdc=amount_usdc,
            locked_until_block=locked_until,
            can_withdraw=can_withdraw,
        )

    chain_status = _try_get_stake_status_on_chain(address)
    if chain_status.get("error") is None:
        return StakeStatusResponse(
            operator_address=address,
            staked=bool(chain_status["staked"]),
            amount_usdc=float(chain_status["amount_usdc"]),
            locked_until_block=chain_status["locked_until_block"],
            can_withdraw=bool(chain_status["can_withdraw"]),
        )

    # Chain layer unavailable — degrade to DB-derived view.
    if rep is None:
        # Seeder with no AgentReputation row yet: treat as registered
        # at the default stake so the UI surfaces a sensible value.
        return StakeStatusResponse(
            operator_address=address,
            staked=True,
            amount_usdc=DEFAULT_AGENT_STAKE_USDC,
            locked_until_block=None,
            can_withdraw=True,
        )
    return StakeStatusResponse(
        operator_address=address,
        staked=True,
        amount_usdc=DEFAULT_AGENT_STAKE_USDC,
        locked_until_block=None,
        can_withdraw=True,
    )


@router.post(
    "/{address}/withdraw-stake",
    response_model=WithdrawStakeResponse,
    summary="Withdraw an operator's unlocked bidding stake",
)
def withdraw_operator_stake(
    address: str,
    payload: Optional[WithdrawStakeRequest] = None,
    session: Session = Depends(get_db),
) -> WithdrawStakeResponse:
    """Withdraw the operator's unlocked auction-contract stake.

    Calls ``TranslationAuction.withdrawStake()`` via the existing
    ``AuctionClient.withdraw_stake`` helper (live mode) or the in-process
    mock ledger (mock mode).

    Failure modes are surfaced as structured HTTP errors:

      * 404 ``no_stake_to_withdraw`` — operator has no unlocked balance.
      * 409 ``stake_locked`` with ``locked_until_block`` — the 72h
        slashable window is still open after a recent auction win.
      * 503 ``rpc_timeout`` — Arc RPC unreachable; the operator should
        retry once chain access is restored.

    Mock mode (``mode="mock"`` in the body, default-safe but caller must
    opt in explicitly via the request body) skips the chain call entirely,
    resets the mock stake ledger, and returns a synthetic ``0xsim_…`` tx
    hash so the UI can demo the settle without burning real testnet gas.
    """

    address = _normalize_address(address)
    alias = _seeder_alias_for(address)
    rep = session.get(AgentReputation, address)
    if rep is None and alias is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="operator_not_found",
        )

    is_mock = (payload.mode if payload else "live") == "mock"
    private_key = payload.private_key if payload else None

    if is_mock:
        ledger_entry = _MOCK_STAKE_LEDGER.get(address.lower())
        # Pre-populate a ledger entry for previously-registered operators
        # so the very first mock-mode withdrawal still surfaces 5 USDC
        # (mirrors what register would have written had it run via the
        # mock path).
        if ledger_entry is None:
            ledger_entry = {
                "amount_usdc": DEFAULT_AGENT_STAKE_USDC,
                "locked_until_block": None,
            }
        locked_until = ledger_entry.get("locked_until_block")
        amount = float(ledger_entry.get("amount_usdc", 0.0))
        if locked_until is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "stake_locked",
                    "locked_until_block": locked_until,
                    "message": (
                        f"Stake locked until block {locked_until}; retry "
                        "after the slashable window closes."
                    ),
                },
            )
        if amount <= 0.0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="no_stake_to_withdraw",
            )
        # Reset the mock ledger entry and emit a synthetic tx hash.
        _MOCK_STAKE_LEDGER[address.lower()] = {
            "amount_usdc": 0.0,
            "locked_until_block": None,
        }
        tx_hash = _make_sim_tx_hash()
        logger.info(
            "operators.withdraw_stake MOCK: address=%s amount=%.6f sim_tx=%s",
            address,
            amount,
            tx_hash,
        )
        return WithdrawStakeResponse(
            success=True,
            tx_hash=tx_hash,
            amount_recovered_usdc=amount,
            is_simulated=True,
            operator_address=address,
        )

    # Live mode — private key is mandatory.
    if not private_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="private_key_required_for_live_withdraw",
        )

    chain_result = _try_withdraw_stake_on_chain(address, private_key)
    error = chain_result.get("error")
    if error == "locked":
        # Surface ``locked_until_block`` if the status helper can read it.
        status_view = _try_get_stake_status_on_chain(address)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "stake_locked",
                "locked_until_block": status_view.get("locked_until_block"),
                "message": (
                    "Stake remains locked under the 72h slashable window; "
                    "retry once it closes."
                ),
            },
        )
    if error == "no_stake":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no_stake_to_withdraw",
        )
    if error == "rpc_timeout":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Arc RPC timed out while submitting withdrawStake; retry "
                "once chain access is restored."
            ),
        )
    if error is not None:
        # Generic chain failure (unavailable, rpc_error, etc.). Bubble up
        # as 503 so the UI can retry rather than treating it as a 4xx bug.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"withdraw_stake_chain_error:{error}",
        )

    tx_hash = chain_result.get("tx_hash")
    amount_recovered = float(chain_result.get("amount_recovered_usdc", 0.0))
    return WithdrawStakeResponse(
        success=True,
        tx_hash=tx_hash,
        amount_recovered_usdc=amount_recovered,
        is_simulated=False,
        operator_address=address,
    )


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
