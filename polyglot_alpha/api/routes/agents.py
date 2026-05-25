"""/agents routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from ...persistence.models import (
    AgentReputation,
    Auction,
    Bid,
    BuilderFeeEvent,
    Translation,
)
from ..deps import get_db

router = APIRouter(prefix="/agents", tags=["agents"])


def _build_history(
    bids: list[Bid],
    wins: list[Auction],
    fees: list[BuilderFeeEvent],
) -> list[dict[str, Any]]:
    """Build a reputation/revenue time series for the UI chart.

    Each point combines the cumulative win-rate proxy with cumulative
    revenue. Output is sorted ascending by timestamp.
    """

    points: list[dict[str, Any]] = []
    cumulative_revenue = 0.0
    fee_iter = sorted(
        [
            (f.timestamp, float(f.fee_amount or 0.0))
            for f in fees
            if f.timestamp is not None
        ],
        key=lambda p: p[0],
    )

    # Use auction settlement timestamps as the reputation axis anchors.
    settled = sorted(
        [w for w in wins if w.settled_at is not None],
        key=lambda w: w.settled_at,  # type: ignore[arg-type, return-value]
    )

    # Walk both streams to produce a unified history.
    for w in settled:
        ts = w.settled_at.isoformat() if w.settled_at else None
        while fee_iter and w.settled_at is not None and fee_iter[0][0] <= w.settled_at:
            cumulative_revenue += fee_iter.pop(0)[1]
        points.append(
            {
                "ts": ts,
                "reputation": min(1.0, 0.5 + 0.05 * (len(points) + 1)),
                "revenue": cumulative_revenue,
            }
        )
    # Tail of remaining fee events past the last win.
    for ts_dt, amount in fee_iter:
        cumulative_revenue += amount
        points.append(
            {
                "ts": ts_dt.isoformat(),
                "reputation": min(1.0, 0.5 + 0.05 * (len(points) + 1)),
                "revenue": cumulative_revenue,
            }
        )
    return points


@router.get("/{address}", summary="Get an agent's reputation row")
def get_agent(
    address: str,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    rep = session.get(AgentReputation, address)
    if rep is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent_not_found"
        )

    wins = session.exec(
        select(Auction)
        .where(Auction.winner_address == address)
        .order_by(Auction.settled_at.desc())
    ).all()
    bids = session.exec(
        select(Bid)
        .where(Bid.agent_address == address)
        .order_by(Bid.submitted_at.desc())
    ).all()
    fees = session.exec(
        select(BuilderFeeEvent)
        .where(BuilderFeeEvent.translator_address == address)
        .order_by(BuilderFeeEvent.timestamp.desc())
    ).all()

    total_wins = int(rep.total_wins)
    total_bids = int(rep.total_bids)
    losses = max(total_bids - total_wins, 0)
    win_rate = (total_wins / total_bids) if total_bids > 0 else 0.0

    return {
        # UI-aligned fields (preferred)
        "address": rep.agent_address,
        "alias": None,
        "reputation": rep.avg_quality,
        "totalRevenue": rep.cumulative_fees,
        "wins": total_wins,
        "losses": losses,
        "winRate": win_rate,
        "history": _build_history(bids, wins, fees),
        # Legacy fields preserved for tests / internal consumers
        "agent_address": rep.agent_address,
        "total_bids": total_bids,
        "total_wins": total_wins,
        "avg_quality": rep.avg_quality,
        "cumulative_fees": rep.cumulative_fees,
        "total_revenue_usdc": rep.cumulative_fees,
        "last_updated": rep.last_updated.isoformat() if rep.last_updated else None,
    }


@router.get(
    "/{address}/history",
    summary="History of bids, wins, translations, and fees for an agent",
)
def get_agent_history(
    address: str,
    session: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    bids = session.exec(
        select(Bid)
        .where(Bid.agent_address == address)
        .order_by(Bid.submitted_at.desc())
        .limit(limit)
    ).all()
    wins = session.exec(
        select(Auction)
        .where(Auction.winner_address == address)
        .order_by(Auction.settled_at.desc())
        .limit(limit)
    ).all()
    translations = session.exec(
        select(Translation)
        .where(Translation.translator_address == address)
        .order_by(Translation.completed_at.desc())
        .limit(limit)
    ).all()
    fees = session.exec(
        select(BuilderFeeEvent)
        .where(BuilderFeeEvent.translator_address == address)
        .order_by(BuilderFeeEvent.timestamp.desc())
        .limit(limit)
    ).all()

    return {
        "agent_address": address,
        "address": address,
        "bids": [
            {
                "event_id": b.event_id,
                "bid_amount": b.bid_amount,
                "submitted_at": b.submitted_at.isoformat() if b.submitted_at else None,
            }
            for b in bids
        ],
        "wins": [
            {
                "event_id": w.event_id,
                "winning_bid": w.winning_bid,
                "settled_at": w.settled_at.isoformat() if w.settled_at else None,
            }
            for w in wins
        ],
        "translations": [
            {
                "event_id": t.event_id,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "pipeline_trace_ipfs": t.pipeline_trace_ipfs,
            }
            for t in translations
        ],
        "fees": [
            {
                "market_id": f.market_id,
                "fee_amount": f.fee_amount,
                "fill_amount": f.fill_amount,
                "is_simulated": f.is_simulated,
                "timestamp": f.timestamp.isoformat() if f.timestamp else None,
            }
            for f in fees
        ],
    }
