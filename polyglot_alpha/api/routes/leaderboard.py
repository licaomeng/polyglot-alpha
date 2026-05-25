"""/leaderboard route."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from ...persistence.models import AgentReputation
from ..deps import get_db

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])

SortKey = Literal["cumulative_fees", "avg_quality", "total_wins", "total_bids"]


def _win_rate(row: AgentReputation) -> float:
    if row.total_bids <= 0:
        return 0.0
    return float(row.total_wins) / float(row.total_bids)


@router.get("", summary="Top translator agents")
def leaderboard(
    session: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=200),
    sort_by: SortKey = Query("cumulative_fees"),
) -> list[dict[str, Any]]:
    """Return a bare JSON array of leaderboard entries (UI contract)."""

    rows = session.exec(select(AgentReputation)).all()
    key_map: dict[str, Any] = {
        "cumulative_fees": lambda r: r.cumulative_fees,
        "avg_quality": lambda r: r.avg_quality,
        "total_wins": lambda r: r.total_wins,
        "total_bids": lambda r: r.total_bids,
    }
    rows_sorted = sorted(rows, key=key_map[sort_by], reverse=True)[:limit]
    return [
        {
            "rank": idx + 1,
            # UI-aligned fields
            "address": r.agent_address,
            "alias": None,
            "reputation": r.avg_quality,
            "revenueUsd": r.cumulative_fees,
            "winRate": _win_rate(r),
            # Legacy fields preserved for downstream/internal consumers
            "agent_address": r.agent_address,
            "total_bids": r.total_bids,
            "total_wins": r.total_wins,
            "avg_quality": r.avg_quality,
            "cumulative_fees": r.cumulative_fees,
        }
        for idx, r in enumerate(rows_sorted)
    ]
