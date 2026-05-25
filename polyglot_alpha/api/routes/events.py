"""/events routes."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from ...persistence.models import (
    AgentReputation,
    Auction,
    Bid,
    EventStatus,
    PolymarketSubmission,
    QualityScore,
    Question,
    Translation,
    Event,
)
from ..deps import get_db

router = APIRouter(prefix="/events", tags=["events"])


# Canonical 7-phase ordering used by both the UI and the SSE workflow.
# Each entry maps the surfaced phase name to the SQLModel column that, when
# non-null, indicates the phase has completed.
_PHASE_NAMES: tuple[str, ...] = (
    "Event Ingestion",
    "USDC Auction",
    "Translation Pipeline",
    "11-Judge Panel",
    "On-chain Anchor",
    "Polymarket V2 Submission",
    "Streaming Revenue",
)


def _serialize_event_summary(event: Event) -> dict[str, Any]:
    """Shape returned to the UI list view.

    Returns a flat object with both legacy fields (``id``, ``status``,
    ``triggered_at``) and UI-friendly aliases (``headline``, ``source``,
    ``ingestedAt``, ``mode``) so the React client can render summaries
    without additional lookups.
    """

    source_name = ""
    if event.sources:
        first = event.sources[0] if isinstance(event.sources, list) else None
        if isinstance(first, dict):
            source_name = str(first.get("name") or first.get("url") or "")
    return {
        "id": str(event.id) if event.id is not None else None,
        "content_hash": event.content_hash,
        "sources": event.sources,
        "source": source_name,
        "language": event.language,
        "title": event.title,
        "headline": event.title,
        "triggered_at": event.triggered_at.isoformat() if event.triggered_at else None,
        "ingestedAt": event.triggered_at.isoformat() if event.triggered_at else None,
        "status": event.status,
        "mode": "mock",
    }


def _phase_status_from_event_status(
    phase_index: int, event_status: str, completed: bool
) -> str:
    """Compute UI phase status (pending/running/completed/failed).

    ``phase_index`` is 0-based against ``_PHASE_NAMES``.
    """

    if event_status in ("REJECTED", "FAILED") and not completed:
        return "failed"
    if completed:
        return "completed"
    # Map current event status to the active phase index.
    status_to_active = {
        "PENDING": 0,
        "AUCTION_OPEN": 1,
        "AUCTION_SETTLED": 1,
        "TRANSLATING": 2,
        "EVALUATING": 3,
        "REJECTED": 3,
        "COMMITTED": 4,
        "SUBMITTED": 5,
        "FAILED": 0,
    }
    active = status_to_active.get(event_status, 0)
    if phase_index == active:
        return "running"
    return "pending"


def _build_phases(
    event: Event,
    auction: Optional[Auction],
    translation: Optional[Translation],
    quality: Optional[QualityScore],
    question: Optional[Question],
    submission: Optional[PolymarketSubmission],
    has_fee_event: bool,
) -> list[dict[str, Any]]:
    """Synthesize 7 phase records reflecting on-chain progress for the UI."""

    completed_flags = [
        True,  # Event Ingestion (always completed once the row exists)
        auction is not None and auction.settled_at is not None,
        translation is not None,
        quality is not None,
        question is not None,
        submission is not None,
        has_fee_event,
    ]
    details: list[dict[str, Any]] = [
        {
            "content_hash": event.content_hash,
            "title": event.title,
            "sources": event.sources,
        },
        {
            "winner_address": getattr(auction, "winner_address", None),
            "winning_bid": getattr(auction, "winning_bid", None),
            "tx_hash": getattr(auction, "settlement_tx_hash", None),
        },
        {
            "translator_address": getattr(translation, "translator_address", None),
            "pipeline_trace_ipfs": getattr(translation, "pipeline_trace_ipfs", None),
        },
        {
            "verdict": getattr(quality, "verdict", None),
            "overall_score": getattr(quality, "overall_score", None),
        },
        {
            "question_id": getattr(question, "question_id_onchain", None),
            "builder_code": getattr(question, "builder_code", None),
            "tx_hash": getattr(question, "tx_hash", None),
            "reasoning_ipfs": getattr(question, "reasoning_ipfs", None),
        },
        {
            "market_id": getattr(submission, "market_id", None),
            "market_url": getattr(submission, "market_url", None),
            "is_simulated": getattr(submission, "is_simulated", None),
        },
        {"streaming": has_fee_event},
    ]
    timestamps: list[Optional[str]] = [
        event.triggered_at.isoformat() if event.triggered_at else None,
        auction.settled_at.isoformat()
        if auction and auction.settled_at
        else None,
        translation.completed_at.isoformat()
        if translation and translation.completed_at
        else None,
        quality.evaluated_at.isoformat()
        if quality and quality.evaluated_at
        else None,
        question.committed_at.isoformat()
        if question and question.committed_at
        else None,
        submission.submitted_at.isoformat()
        if submission and submission.submitted_at
        else None,
        None,
    ]

    phases: list[dict[str, Any]] = []
    for idx, name in enumerate(_PHASE_NAMES):
        completed = bool(completed_flags[idx])
        phase_status = _phase_status_from_event_status(
            idx, event.status, completed
        )
        phases.append(
            {
                "id": f"phase-{idx + 1}",
                "name": name,
                "status": phase_status,
                "timestamp": timestamps[idx],
                "completedAt": timestamps[idx] if completed else None,
                "tx_hash": (
                    details[idx].get("tx_hash") if isinstance(details[idx], dict) else None
                ),
                "details": details[idx],
            }
        )
    return phases


def _serialize_event_detail(
    session: Session, event: Event
) -> dict[str, Any]:
    """Build the rich event detail object expected by the UI."""

    auction = session.get(Auction, event.id)
    translation = session.get(Translation, event.id)
    quality = session.get(QualityScore, event.id)
    question = session.exec(
        select(Question).where(Question.event_id == event.id)
    ).first()
    submission = session.exec(
        select(PolymarketSubmission)
        .where(PolymarketSubmission.event_id == event.id)
        .order_by(PolymarketSubmission.id.desc())
    ).first()
    bids = session.exec(
        select(Bid)
        .where(Bid.event_id == event.id)
        .order_by(Bid.bid_amount.desc())
    ).all()

    # Pull current reputation for each bidder to enrich the bids array.
    rep_by_address: dict[str, AgentReputation] = {}
    for b in bids:
        if b.agent_address in rep_by_address:
            continue
        rep = session.get(AgentReputation, b.agent_address)
        if rep is not None:
            rep_by_address[b.agent_address] = rep

    # Builder-fee presence indicates the Streaming Revenue phase completed.
    from ...persistence.models import BuilderFeeEvent

    has_fee_event = False
    if submission is not None and submission.market_id:
        any_fee = session.exec(
            select(BuilderFeeEvent).where(
                BuilderFeeEvent.market_id == submission.market_id
            )
        ).first()
        has_fee_event = any_fee is not None

    # Build the anchor object the UI consumes for the On-chain Anchor phase.
    # Falls back to the auction settlement tx when no question row exists yet
    # (e.g. REJECTED events that never anchored). Explorer URL points at the
    # public Arc testnet explorer so demo viewers can verify the tx.
    anchor_tx_hash = (
        getattr(question, "tx_hash", None)
        or getattr(auction, "settlement_tx_hash", None)
    )
    anchor: Optional[dict[str, Any]] = None
    if anchor_tx_hash:
        anchor = {
            "txHash": anchor_tx_hash,
            "explorerUrl": f"https://testnet.arcscan.app/tx/{anchor_tx_hash}",
            "ipfsCid": getattr(question, "reasoning_ipfs", None),
        }

    detail: dict[str, Any] = _serialize_event_summary(event)
    detail.update(
        {
            "winner_address": getattr(auction, "winner_address", None),
            "winning_bid": getattr(auction, "winning_bid", None),
            "verdict": getattr(quality, "verdict", None),
            "overall_score": getattr(quality, "overall_score", None),
            "anchor": anchor,
            "translation_scores": (
                quality.translation_scores if quality is not None else None
            ),
            "style_alignment_passes": (
                quality.style_alignment_passes if quality is not None else None
            ),
            "question_id": getattr(question, "question_id_onchain", None),
            "builder_code": getattr(question, "builder_code", None),
            "market_id": getattr(submission, "market_id", None),
            "market_url": getattr(submission, "market_url", None),
            "is_simulated": (
                bool(submission.is_simulated) if submission is not None else None
            ),
            "final_question": (
                translation.final_question_json if translation is not None else None
            ),
            "phases": _build_phases(
                event,
                auction,
                translation,
                quality,
                question,
                submission,
                has_fee_event,
            ),
            "bids": [
                {
                    "agent_address": b.agent_address,
                    "agent": b.agent_address,
                    "bid_amount": b.bid_amount,
                    "bid": b.bid_amount,
                    "stake_amount": b.stake_amount,
                    "candidate_hash": b.candidate_hash,
                    "tx_hash": b.tx_hash,
                    "submitted_at": b.submitted_at.isoformat()
                    if b.submitted_at
                    else None,
                    "reputation": (
                        rep_by_address[b.agent_address].avg_quality
                        if b.agent_address in rep_by_address
                        else 0.0
                    ),
                    "winner": bool(
                        auction is not None
                        and auction.winner_address == b.agent_address
                    ),
                }
                for b in bids
            ],
        }
    )
    return detail


@router.get("", summary="List events")
def list_events(
    session: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status_filter: EventStatus | None = Query(None, alias="status"),
) -> list[dict[str, Any]]:
    """Return a bare JSON array of event summaries (UI contract).

    ``status`` is validated as a strict :class:`EventStatus` enum so
    invalid values (e.g. ``?status=BOGUS``) return HTTP 422 rather than
    being silently treated as ``None``.
    """

    stmt = select(Event)
    if status_filter is not None:
        stmt = stmt.where(Event.status == status_filter.value)
    stmt = stmt.order_by(Event.triggered_at.desc()).offset(offset).limit(limit)
    rows = session.exec(stmt).all()
    return [_serialize_event_summary(e) for e in rows]


@router.get("/{event_id}", summary="Get event by id")
def get_event(
    event_id: int,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="event_not_found"
        )
    return _serialize_event_detail(session, event)


@router.get("/{event_id}/bids", summary="List bids for an event")
def list_bids_for_event(
    event_id: int,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="event_not_found"
        )
    bids = session.exec(
        select(Bid).where(Bid.event_id == event_id).order_by(Bid.bid_amount.desc())
    ).all()
    return {
        "event_id": event_id,
        "items": [
            {
                "id": b.id,
                "agent_address": b.agent_address,
                "bid_amount": b.bid_amount,
                "stake_amount": b.stake_amount,
                "candidate_hash": b.candidate_hash,
                "tx_hash": b.tx_hash,
                "submitted_at": b.submitted_at.isoformat() if b.submitted_at else None,
            }
            for b in bids
        ],
    }


@router.get("/{event_id}/phases", summary="List phase records for an event")
def list_phases_for_event(
    event_id: int,
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return the 7-phase array used by the UI workflow overview."""

    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="event_not_found"
        )
    detail = _serialize_event_detail(session, event)
    return list(detail.get("phases") or [])


@router.get("/{event_id}/translations", summary="List translations for an event")
def list_translations_for_event(
    event_id: int,
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return the translation rows persisted for ``event_id``."""

    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="event_not_found"
        )
    translations = session.exec(
        select(Translation).where(Translation.event_id == event_id)
    ).all()
    return [
        {
            "event_id": t.event_id,
            "translator_address": t.translator_address,
            "pipeline_trace_ipfs": t.pipeline_trace_ipfs,
            "final_question_json": t.final_question_json,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in translations
    ]
