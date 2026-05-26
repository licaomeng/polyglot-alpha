"""/trigger demo endpoint — kicks off the orchestrator for a sample event."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlmodel import select

from ...orchestrator import BidRecord, create_pending_event, run_lifecycle
from ...persistence import session_scope
from ...persistence.models import Event
from ...pubsub import get_pubsub
from ..rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trigger", tags=["trigger"])

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HARDCODED_SAMPLE_PATH = _REPO_ROOT / "outputs" / "sample_0.json"


# Last-resort demo headline used when neither the RSS pipeline nor the
# bundled sample_0.json are available. Lets the demo button always
# produce a 200 even on a freshly cloned checkout.
_DEMO_FALLBACK_TITLE: str = (
    "Will the People's Bank of China announce a Reserve Requirement Ratio "
    "cut before December 31, 2026?"
)
_DEMO_FALLBACK_SOURCES: list[dict[str, str]] = [
    {
        "name": "pbc.gov.cn",
        "url": "http://www.pbc.gov.cn/",
        "language": "zh",
    }
]

# Placeholder title written into the PENDING events row while the
# background RSS fetch + Haiku scoring is still in flight. Once the
# real headline lands, the row is updated and ``event.created`` is
# republished so SSE listeners refresh the title in the UI.
_RSS_PLACEHOLDER_TITLE: str = "Fetching latest non-English news…"


# ---------------------------------------------------------------------------
# Input size limits (DoS hardening)
# ---------------------------------------------------------------------------

MAX_TITLE_LENGTH: int = 500
MAX_BIDS_PER_REQUEST: int = 20
MAX_SOURCES_PER_REQUEST: int = 10
MIN_AUCTION_WINDOW_S: float = 0.0
MAX_AUCTION_WINDOW_S: float = 300.0

# Bid amount sanity bounds — positive, sane upper bound.
MIN_BID_AMOUNT: float = 0.0001
MAX_BID_AMOUNT: float = 10000.0

# Stake bounds (defensive — same upper bound as bid).
MIN_STAKE_AMOUNT: float = 0.0
MAX_STAKE_AMOUNT: float = 10000.0

# Reputation is a normalized score in ``[0.0, 1.0]``.
MIN_REPUTATION: float = 0.0
MAX_REPUTATION: float = 1.0

# Agent address pattern. Hex form (``0x[a-fA-F0-9]+``) is the canonical
# Ethereum-ish shape; we also tolerate purely lowercase test/demo strings
# (``0xagent``, ``0xllama_agent``) since the orchestrator treats the
# address as an opaque identifier.
_AGENT_ADDRESS_RE: re.Pattern[str] = re.compile(r"^0x[a-zA-Z0-9_]+$")


class TriggerSource(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., min_length=1, max_length=2000)
    language: str = Field(default="en", max_length=16)


class TriggerBid(BaseModel):
    """Pydantic-validated bid record. Mirrors :class:`BidRecord`."""

    agent_address: str = Field(..., min_length=1, max_length=128)
    bid_amount: float = Field(
        ..., ge=MIN_BID_AMOUNT, le=MAX_BID_AMOUNT
    )
    stake_amount: float = Field(
        default=5.0, ge=MIN_STAKE_AMOUNT, le=MAX_STAKE_AMOUNT
    )
    candidate_hash: str | None = Field(default=None, max_length=128)
    tx_hash: str | None = Field(default=None, max_length=128)
    reputation: float = Field(
        default=1.0, ge=MIN_REPUTATION, le=MAX_REPUTATION
    )

    @field_validator("agent_address")
    @classmethod
    def _validate_agent_address(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("agent_address must be non-empty")
        if not _AGENT_ADDRESS_RE.match(value):
            raise ValueError(
                "agent_address must match ^0x[a-zA-Z0-9_]+$"
            )
        return value

    @field_validator("bid_amount", "stake_amount", "reputation")
    @classmethod
    def _reject_non_finite(cls, value: float) -> float:
        # Pydantic accepts ``float('nan')`` / ``float('inf')`` even with
        # ``ge``/``le`` bounds on some versions; reject them explicitly.
        if not math.isfinite(value):
            raise ValueError("value must be a finite number")
        return value


_VALID_EVENT_SOURCES = ("user_payload", "hardcoded", "rss")

# Lifecycle ``mode`` values accepted by ``POST /trigger/event``. ``live`` runs
# the real LLM + Arc tx + RSS + judge panel; ``mock`` short-circuits each
# subsystem with deterministic fixtures (other W5 agents own those impls).
_VALID_EVENT_MODES: tuple[str, ...] = ("live", "mock")
_DEFAULT_EVENT_MODE_ENV: str = "DEFAULT_EVENT_MODE"
_FALLBACK_DEFAULT_EVENT_MODE: str = "live"


def _resolve_default_event_mode() -> str:
    """Return the env-configured default mode, falling back to ``"live"``."""

    raw = os.environ.get(_DEFAULT_EVENT_MODE_ENV, _FALLBACK_DEFAULT_EVENT_MODE)
    candidate = (raw or _FALLBACK_DEFAULT_EVENT_MODE).strip().lower()
    if candidate not in _VALID_EVENT_MODES:
        logger.warning(
            "trigger: %s=%r is invalid; falling back to %r",
            _DEFAULT_EVENT_MODE_ENV,
            raw,
            _FALLBACK_DEFAULT_EVENT_MODE,
        )
        return _FALLBACK_DEFAULT_EVENT_MODE
    return candidate


class TriggerRequest(BaseModel):
    # ``title`` is required for ``user_payload`` mode but becomes optional
    # for ``rss``/``hardcoded`` where the backend fills it in. We keep the
    # field non-empty when present to preserve the historical validation.
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    sources: list[TriggerSource] = Field(
        default_factory=list, max_length=MAX_SOURCES_PER_REQUEST
    )
    language: str = Field(default="en", max_length=16)
    category: str = Field(default="geopolitics", max_length=64)
    event_source: str = Field(
        default="user_payload",
        description=(
            "Where the event comes from. ``user_payload`` (default) trusts "
            "the request body. ``hardcoded`` reads outputs/sample_0.json. "
            "``rss`` fetches the freshest cross-referenced cluster from "
            "the configured RSS sources and falls back to ``hardcoded`` "
            "when no cluster confirms within ``rss_window_minutes``."
        ),
    )
    rss_window_minutes: int = Field(
        default=60, ge=1, le=24 * 60,
        description="How far back to look for RSS items when event_source='rss'.",
    )
    auction_mode: str | None = Field(
        default=None,
        description=(
            "Override AUCTION_MODE for this lifecycle. ``real`` drives 4 "
            "agent bids; ``mock`` uses the legacy deterministic bid path."
        ),
    )
    confirm_real_polymarket: bool = Field(
        default=False,
        description=(
            "Operator opt-in for live Polymarket submission. Without it, "
            "real-mode degrades to a ``blocked`` SubmissionResult."
        ),
    )
    auction_window_seconds: float | None = Field(
        default=0.0,
        ge=MIN_AUCTION_WINDOW_S,
        le=MAX_AUCTION_WINDOW_S,
        description=(
            "Auction window in seconds. Defaults to 0 in the demo endpoint "
            "so the lifecycle completes synchronously."
        ),
    )
    mock_bids: list[TriggerBid] | None = Field(
        default=None,
        max_length=MAX_BIDS_PER_REQUEST,
        description=(
            "Optional override list of {agent_address, bid_amount, ...}. "
            "When omitted, the orchestrator drives a real on-chain auction "
            "across the 4 reference agents (auction_mode='real')."
        ),
    )
    # ``mode`` is the W5 lifecycle mode (``"live"`` | ``"mock"``). We accept
    # it as a free-form string here and validate in the handler so the
    # 400-on-invalid contract isn't masked by Pydantic's 422 default.
    # When omitted, the handler falls back to the ``DEFAULT_EVENT_MODE``
    # env var (and ultimately to ``"live"``) to preserve back-compat.
    mode: str | None = Field(
        default=None,
        max_length=16,
        description=(
            "Lifecycle execution mode for this event. ``live`` (default) "
            "runs the real LLM + Arc tx + RSS + judge panel. ``mock`` "
            "short-circuits each subsystem with deterministic fixtures. "
            "Invalid values return HTTP 400."
        ),
    )

    @field_validator("event_source")
    @classmethod
    def _validate_event_source(cls, value: str) -> str:
        v = (value or "user_payload").strip().lower()
        if v not in _VALID_EVENT_SOURCES:
            raise ValueError(
                f"event_source must be one of {_VALID_EVENT_SOURCES}"
            )
        return v

    @field_validator("auction_mode")
    @classmethod
    def _validate_auction_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        v = value.strip().lower()
        if v not in ("real", "mock"):
            raise ValueError("auction_mode must be 'real' or 'mock'")
        return v
    run_in_background: bool = Field(
        default=False,
        description=(
            "If true the lifecycle runs as a FastAPI BackgroundTask and "
            "the endpoint returns immediately with {scheduled: true}."
        ),
    )

    @field_validator("mock_bids")
    @classmethod
    def _reject_duplicate_addresses(
        cls, value: list[TriggerBid] | None
    ) -> list[TriggerBid] | None:
        if not value:
            return value
        seen: set[str] = set()
        for bid in value:
            if bid.agent_address in seen:
                raise ValueError(
                    f"duplicate agent_address in mock_bids: {bid.agent_address}"
                )
            seen.add(bid.agent_address)
        return value


def _load_hardcoded_sample() -> dict[str, Any] | None:
    """Read ``outputs/sample_0.json`` and project it into the trigger shape.

    Returns ``None`` if the file is missing or malformed so the caller can
    fall back to the in-process demo defaults.
    """

    if not _HARDCODED_SAMPLE_PATH.exists():
        return None
    try:
        with _HARDCODED_SAMPLE_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "trigger: failed to read hardcoded sample (%s): %s",
            _HARDCODED_SAMPLE_PATH,
            exc,
        )
        return None
    title = str(data.get("title") or "").strip()
    if not title:
        return None
    resolution = data.get("resolution_source") or ""
    sources: list[dict[str, str]] = []
    if resolution:
        sources.append(
            {
                "name": "resolution_source",
                "url": str(resolution),
                "language": str(data.get("source_language") or "zh"),
            }
        )
    return {
        "title": title,
        "sources": sources or list(_DEMO_FALLBACK_SOURCES),
        "language": str(data.get("source_language") or "zh"),
        "category": str(data.get("category") or "geopolitics"),
        "summary": data.get("source_news") or data.get("description"),
    }


def _fallback_demo_event() -> dict[str, Any]:
    """Return the last-resort hardcoded demo event (never fails)."""

    return {
        "title": _DEMO_FALLBACK_TITLE,
        "sources": list(_DEMO_FALLBACK_SOURCES),
        "language": "zh",
        "category": "policy/china",
    }


async def _fetch_rss_demo_event(window_minutes: int) -> dict[str, Any] | None:
    """Best-effort RSS fetch → Haiku event scoring → raw cluster passthrough.

    Post-pivot contract (2026-05-26): the marketplace MUST NOT write any
    Polymarket question text. Each agent (seeder or external) frames its
    own question during the auction. This function therefore returns the
    RAW news cluster (title, summary, sources) plus a ``scoring`` dict
    with quality / category / entities metadata — nothing more.

    1. Poll the registered RSS sources once.
    2. Filter to items within ``window_minutes``.
    3. Ask Claude Haiku 4.5 to score the cluster (quality, category,
       entities, credibility, timeliness) — NOT to write a question.
    4. If ``event_quality_score`` is below the auction threshold, return
       ``None`` so the caller degrades to the hardcoded fallback.
    5. Else return ``{title, sources, language, category, summary,
       scoring}`` where ``scoring`` is the :class:`EventScoring` dict.

    Returns ``None`` on RSS failure or sub-threshold score so the caller
    degrades to the bundled hardcoded sample.

    Mock-mode short-circuit (W5-A3): when the lifecycle contextvar
    ``event_mode == "mock"`` (set by the trigger handler / orchestrator
    after :func:`set_event_mode`), we skip the live RSS poll entirely
    and pick a canned multi-language cluster from
    :mod:`polyglot_alpha.ingestion.fixtures`. The fixture matches the
    same return shape as the real path so the caller is fully agnostic.
    """

    # ---- W5-A3 fixture short-circuit ----
    try:
        from polyglot_alpha.logging_ctx import get_event_mode
    except ImportError:  # pragma: no cover - defensive
        get_event_mode = None  # type: ignore[assignment]
    if get_event_mode is not None and get_event_mode() == "mock":
        try:
            from polyglot_alpha.ingestion.fixtures import pick_mock_cluster

            cluster = pick_mock_cluster()
            logger.info(
                "trigger: mock-mode short-circuit — fixture title=%r",
                (cluster.get("title") or "")[:80],
            )
            return {
                "title": cluster["title"],
                "sources": list(cluster.get("sources") or []),
                "language": cluster.get("language", "zh"),
                "category": cluster.get("category", "geopolitics"),
                "summary": cluster.get("summary"),
                "scoring": cluster.get("scoring"),
            }
        except (FileNotFoundError, RuntimeError, ImportError) as exc:
            logger.warning(
                "trigger: mock fixture load failed (%s); falling through to RSS",
                exc,
            )

    try:
        from polyglot_alpha.ingestion import (
            cross_reference,
            news_summarizer,
            rss_aggregator,
        )
    except ImportError as exc:
        logger.info("trigger: ingestion modules unavailable (%s)", exc)
        return None

    # ---- 1. Poll RSS ----
    try:
        sources = rss_aggregator.load_sources()
        raw_events = await rss_aggregator.poll_sources_once(sources)
    except Exception as exc:  # broad — outbound HTTP / parse / FS
        logger.warning("trigger: RSS poll failed (%s)", exc)
        return None

    if not raw_events:
        logger.info("trigger: RSS poll returned no events")
        return None

    # ---- 2. Recency filter ----
    try:
        recent = cross_reference.filter_recent(
            raw_events, window=timedelta(minutes=window_minutes)
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("trigger: recency filter crashed (%s)", exc)
        recent = []
    # If nothing landed in the requested window, widen to "anything we got"
    # so the demo button still surfaces a real headline rather than the
    # canned fallback. The 5-min DB dedup downstream prevents duplicates.
    pool = recent if recent else raw_events

    # ---- 3. Format for Haiku and score the cluster ----
    articles: list[dict[str, Any]] = []
    for ev in pool[:25]:  # cap so prompt stays small
        articles.append(
            {
                "title": ev.title,
                "summary": ev.summary,
                "source": ev.source,
                "published": ev.published_at.isoformat()
                if ev.published_at
                else "",
                "url": ev.url,
                "language": ev.language,
            }
        )

    if not articles:
        return None

    scoring = await news_summarizer.score_event_for_auction(articles)

    # ---- 4. Threshold check — reject low-quality clusters ----
    if scoring.event_quality_score < news_summarizer.MIN_AUCTION_QUALITY:
        logger.info(
            "trigger: cluster rejected — score=%.2f reason=%r",
            scoring.event_quality_score,
            scoring.rejection_reason,
        )
        return None

    # ---- 5. Pass through the RAW top article — no question rewriting ----
    top = articles[0]
    language = top.get("language") or "zh"

    return {
        "title": top["title"],
        "sources": [
            {
                "name": top["source"],
                "url": top["url"] or "",
                "language": language,
            }
        ],
        "language": language,
        "category": scoring.primary_category or "geopolitics",
        "summary": scoring.raw_summary or top["summary"],
        "scoring": scoring.as_dict(),
    }


def _coerce_bids(raw: list[TriggerBid] | None) -> list[BidRecord] | None:
    """Convert validated :class:`TriggerBid` Pydantic models into orchestrator
    :class:`BidRecord` dataclasses. All sanity checks happened at Pydantic
    validation time; this is a straight 1:1 projection.
    """

    if raw is None:
        return None
    out: list[BidRecord] = []
    for bid in raw:
        out.append(
            BidRecord(
                agent_address=bid.agent_address,
                bid_amount=bid.bid_amount,
                stake_amount=bid.stake_amount,
                candidate_hash=bid.candidate_hash,
                tx_hash=bid.tx_hash,
                reputation=bid.reputation,
            )
        )
    return out


@router.post("/event", summary="Trigger a new event lifecycle (demo)")
@limiter.limit("10/minute")
async def trigger_event(
    request: Request,
    payload: TriggerRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    # --- Resolve the W5 lifecycle mode (live | mock) ---------------------
    # Validate explicitly here so we can return HTTP 400 (not 422) on
    # ``mode='bogus'`` per the W5-A1 contract. If the client omits ``mode``
    # entirely we fall back to ``DEFAULT_EVENT_MODE`` and then to ``"live"``.
    if payload.mode is None:
        lifecycle_mode = _resolve_default_event_mode()
    else:
        candidate = (payload.mode or "").strip().lower()
        if candidate not in _VALID_EVENT_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"mode must be one of {list(_VALID_EVENT_MODES)}; "
                    f"got {payload.mode!r}"
                ),
            )
        lifecycle_mode = candidate

    # --- W5-A3 mock short-circuit ----------------------------------------
    # In ``mode='mock'`` the lifecycle never touches the live RSS feed,
    # Haiku scorer, or any external network. We pick one of the 5 bundled
    # multi-language news clusters and run it through the full lifecycle.
    # This bypasses the ``event_source`` branching below entirely because
    # we already have the resolved event body (title, sources, language,
    # category, summary, scoring) in hand from the fixture.
    if lifecycle_mode == "mock":
        from ...ingestion.fixtures import pick_mock_cluster  # local import to
        # keep cold-start light on the live path

        try:
            cluster = pick_mock_cluster()
        except (FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"mock fixture load failed: {exc}",
            ) from exc

        try:
            bids = _coerce_bids(payload.mock_bids)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.errors(),
            ) from exc

        # Build event_dict in the same shape ``_fetch_rss_demo_event``
        # would have produced — :func:`run_lifecycle` is the source of
        # truth on the contract so we just pass the fixture through.
        mock_event_dict: dict[str, Any] = {
            "title": cluster["title"],
            "sources": list(cluster.get("sources") or []),
            "language": cluster.get("language", "zh"),
            "category": cluster.get("category", "geopolitics"),
        }
        if cluster.get("summary") is not None:
            mock_event_dict["summary"] = cluster["summary"]
        if isinstance(cluster.get("scoring"), dict):
            mock_event_dict["scoring"] = cluster["scoring"]

        # Salt the title with a per-click token so repeated mock triggers
        # always produce a NEW lifecycle (content_hash dedup would
        # otherwise collapse every click in the same minute back onto the
        # first event_id, breaking the demo button's "fresh run on each
        # click" expectation). Suffix is hidden inside square brackets so
        # the UI still shows a clean headline.
        unique_token = uuid.uuid4().hex[:8]
        mock_event_dict["title"] = f"{mock_event_dict['title']} [mock:{unique_token}]"

        prep = await create_pending_event(mock_event_dict, mode=lifecycle_mode)
        precreated_event_id_mock: int = int(prep["event_id"])

        async def _mock_runner() -> None:
            await run_lifecycle(
                mock_event_dict,
                auction_window_seconds=payload.auction_window_seconds,
                mock_bids=bids,
                # Force ``auction_mode='mock'`` in mock mode so the
                # auction sub-system also uses its deterministic seeder
                # path (no external agent dispatch / on-chain calls).
                auction_mode=payload.auction_mode or "mock",
                confirm_real_polymarket=payload.confirm_real_polymarket,
                precreated_event_id=precreated_event_id_mock,
                mode=lifecycle_mode,
            )

        background_tasks.add_task(_mock_runner)
        return {
            "event_id": precreated_event_id_mock,
            "status": "PENDING",
            "scheduled": True,
            "title": mock_event_dict["title"],
            "mode": lifecycle_mode,
        }

    # --- Resolve the event body depending on event_source -----------------
    # ``user_payload`` (default) — trust the body. Title is mandatory.
    # ``hardcoded``                — read outputs/sample_0.json.
    # ``rss``                      — fetch latest RSS cluster; on any
    #                                failure degrade to ``hardcoded`` and
    #                                finally to a baked-in fallback.
    source_mode = (payload.event_source or "user_payload").lower()
    resolved_title: str | None = payload.title
    resolved_sources: list[dict[str, Any]] = [
        s.model_dump() for s in payload.sources
    ]
    resolved_language: str = payload.language
    resolved_category: str = payload.category
    resolved_summary: Any = None
    resolved_scoring: dict[str, Any] | None = None

    if source_mode == "rss":
        # Demo-grade fast path for RSS: we do NOT block the HTTP response
        # on the 5-15 s RSS fetch + Haiku scoring. Instead we pre-create a
        # PENDING event row with a placeholder title and schedule a single
        # BackgroundTask that:
        #   1. Runs ``_fetch_rss_demo_event(...)`` (RSS poll + Haiku score)
        #   2. UPDATEs the events row with the real title / sources / lang.
        #   3. Re-publishes ``event.created`` so SSE listeners refresh.
        #   4. Calls ``run_lifecycle(precreated_event_id=event_id)``.
        # This lets the UI navigate to ``/events/{id}`` in <200 ms and
        # render the news-fetch step as a regular SSE timeline phase.
        try:
            bids = _coerce_bids(payload.mock_bids)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.errors(),
            ) from exc

        # Salt the placeholder title with a per-click unique token so every
        # button click creates a NEW event row (no dedup on placeholders).
        # The earlier 5-min-epoch-bucket salt caused repeated clicks within
        # 5 minutes to all map to the same content_hash and dedup back to
        # the first event_id — that broke the user's expectation of "every
        # click runs a fresh demo". Real-content dedup still happens later
        # in the background task when run_lifecycle computes the content_hash
        # over the resolved RSS headline + sources.
        unique_token = uuid.uuid4().hex
        placeholder_title = f"{_RSS_PLACEHOLDER_TITLE} [{unique_token}]"
        placeholder_dict: dict[str, Any] = {
            "title": placeholder_title,
            "sources": resolved_sources or list(_DEMO_FALLBACK_SOURCES),
            "language": payload.language or "zh",
            "category": payload.category or "geopolitics",
        }
        prep = await create_pending_event(placeholder_dict, mode=lifecycle_mode)
        precreated_event_id_rss: int = int(prep["event_id"])

        async def _rss_then_lifecycle() -> None:
            rss_event = await _fetch_rss_demo_event(payload.rss_window_minutes)
            if rss_event is None:
                rss_event = _load_hardcoded_sample() or _fallback_demo_event()
                logger.info(
                    "trigger: event_source=rss degraded to hardcoded fallback"
                )
            real_title = rss_event.get("title") or _DEMO_FALLBACK_TITLE
            real_sources = list(
                rss_event.get("sources") or _DEMO_FALLBACK_SOURCES
            )
            real_language = str(rss_event.get("language") or "zh")
            real_category = str(rss_event.get("category") or "geopolitics")
            real_summary = rss_event.get("summary")
            real_scoring = rss_event.get("scoring")

            # UPDATE the pre-created event row in-place so subsequent
            # ``GET /events/{id}`` calls return the real headline.
            try:
                with session_scope() as session:
                    row = session.get(Event, precreated_event_id_rss)
                    if row is not None:
                        row.title = real_title
                        row.sources = real_sources
                        row.language = real_language
                        session.add(row)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "trigger: failed to update RSS placeholder row id=%s: %s",
                    precreated_event_id_rss,
                    exc,
                )

            # Re-publish ``event.created`` with the real title so SSE
            # subscribers can refresh the header. We include both the new
            # title and the original placeholder so UI can detect updates.
            try:
                hub = get_pubsub()
                await hub.publish(
                    "event.updated",
                    {
                        "event_id": precreated_event_id_rss,
                        "title": real_title,
                        "scoring": real_scoring if isinstance(real_scoring, dict) else None,
                    },
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "trigger: pubsub republish failed (%s)", exc
                )

            # Now run the lifecycle with the real event_dict. We pass
            # ``precreated_event_id`` so the orchestrator adopts the same
            # row rather than inserting a duplicate.
            final_event_dict: dict[str, Any] = {
                "title": real_title,
                "sources": real_sources,
                "language": real_language,
                "category": real_category,
            }
            if real_summary is not None:
                final_event_dict["summary"] = real_summary
            if isinstance(real_scoring, dict):
                final_event_dict["scoring"] = real_scoring

            await run_lifecycle(
                final_event_dict,
                auction_window_seconds=payload.auction_window_seconds,
                mock_bids=bids,
                auction_mode=payload.auction_mode,
                confirm_real_polymarket=payload.confirm_real_polymarket,
                precreated_event_id=precreated_event_id_rss,
                mode=lifecycle_mode,
            )

        background_tasks.add_task(_rss_then_lifecycle)
        return {
            "event_id": precreated_event_id_rss,
            "status": "PENDING",
            "scheduled": True,
            "title": placeholder_title,
            "mode": lifecycle_mode,
        }
    elif source_mode == "hardcoded":
        hard = _load_hardcoded_sample() or _fallback_demo_event()
        resolved_title = hard.get("title") or _DEMO_FALLBACK_TITLE
        resolved_sources = list(hard.get("sources") or _DEMO_FALLBACK_SOURCES)
        resolved_language = str(hard.get("language") or "zh")
        resolved_category = str(hard.get("category") or "policy/china")
        resolved_summary = hard.get("summary")
    else:
        # ``user_payload`` -> title is required.
        if not resolved_title or not resolved_title.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=[
                    {
                        "loc": ["body", "title"],
                        "msg": "title is required when event_source='user_payload'",
                        "type": "value_error.missing",
                    }
                ],
            )

    event_dict: dict[str, Any] = {
        "title": resolved_title,
        "sources": resolved_sources,
        "language": resolved_language,
        "category": resolved_category,
    }
    if resolved_summary is not None:
        event_dict["summary"] = resolved_summary
    if resolved_scoring is not None:
        event_dict["scoring"] = resolved_scoring
    try:
        bids = _coerce_bids(payload.mock_bids)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc

    # 5-minute sliding-window dedup. The orchestrator's content_hash dedup
    # is permanent (per `compute_content_hash` over title+sources+language)
    # which makes the RSS demo unusable since the same Haiku-picked
    # headline can recur for ~30 min. Pre-check the events table for a row
    # with the same title created in the last 5 minutes — if hit, short-
    # circuit to that event_id (acts like dedup but doesn't 409). Older
    # duplicates are salted with a 5-min epoch bucket so they progress as
    # a fresh lifecycle.
    if resolved_title:
        recent_cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
        with session_scope() as session:
            same_title_recent = session.exec(
                select(Event)
                .where(Event.title == resolved_title)
                .where(Event.triggered_at >= recent_cutoff.replace(tzinfo=None))
                .order_by(Event.triggered_at.desc())
            ).first()
            if same_title_recent is not None and same_title_recent.id is not None:
                logger.info(
                    "trigger: 5-min dedup hit — reusing event_id=%s for title=%r",
                    same_title_recent.id,
                    resolved_title[:80],
                )
                return {
                    "event_id": same_title_recent.id,
                    "status": same_title_recent.status,
                    "scheduled": False,
                    "deduped": True,
                    "mode": same_title_recent.mode or "live",
                }

        # NOTE: the previous ``source_mode == "rss"`` 5-min-bucket salt that
        # lived here was removed when the RSS branch was inverted into a
        # placeholder + BackgroundTask flow (the fetch now runs *after* the
        # response). RSS triggers never reach this block.

    # Demo-grade fast path: pre-create the PENDING event row so the caller
    # gets ``event_id`` back in ~10 ms and can navigate to ``/events/{id}``
    # while the 60-90 s lifecycle (auction → translation → 11-judge panel →
    # Arc anchor → Polymarket) runs as a FastAPI BackgroundTask. The UI
    # detail page subscribes to SSE and animates the Timeline as each
    # lifecycle phase publishes its event.
    prep = await create_pending_event(event_dict, mode=lifecycle_mode)
    if prep.get("deduped"):
        # Permanent content_hash dedup still fires for back-to-back
        # identical payloads >5 min apart that survived our salt. Surface
        # as 200 with the original event_id so the demo button still
        # navigates the user somewhere useful.
        logger.info(
            "trigger: content_hash dedup — reusing event_id=%s",
            prep.get("event_id"),
        )
        return {
            "event_id": prep.get("event_id"),
            "status": prep.get("status"),
            "scheduled": False,
            "deduped": True,
            "mode": prep.get("mode") or "live",
        }
    precreated_event_id: int = int(prep["event_id"])

    if payload.run_in_background:
        async def _runner() -> None:
            await run_lifecycle(
                event_dict,
                auction_window_seconds=payload.auction_window_seconds,
                mock_bids=bids,
                auction_mode=payload.auction_mode,
                confirm_real_polymarket=payload.confirm_real_polymarket,
                precreated_event_id=precreated_event_id,
                mode=lifecycle_mode,
            )

        background_tasks.add_task(_runner)
        return {
            "scheduled": True,
            "event_id": precreated_event_id,
            "status": "PENDING",
            "title": resolved_title,
            "mode": lifecycle_mode,
        }

    # Default demo path: schedule lifecycle in the background so the
    # endpoint returns event_id immediately. The UI navigates to the
    # detail page on event_id and watches SSE for phase transitions.
    async def _bg_runner() -> None:
        await run_lifecycle(
            event_dict,
            auction_window_seconds=payload.auction_window_seconds,
            mock_bids=bids,
            auction_mode=payload.auction_mode,
            confirm_real_polymarket=payload.confirm_real_polymarket,
            precreated_event_id=precreated_event_id,
            mode=lifecycle_mode,
        )

    background_tasks.add_task(_bg_runner)
    return {
        "event_id": precreated_event_id,
        "status": "PENDING",
        "scheduled": True,
        "mode": lifecycle_mode,
    }
