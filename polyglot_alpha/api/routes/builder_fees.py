"""/builder_fees route — paginated list of accrued builder-fee events."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from ...persistence.models import BuilderFeeEvent
from ..deps import get_db

router = APIRouter(prefix="/builder_fees", tags=["builder_fees"])


@router.get("", summary="List builder-fee events")
def list_builder_fees(
    session: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    market_id: str | None = Query(None, description="Filter by market id"),
    translator_address: str | None = Query(
        None, description="Filter by translator address"
    ),
) -> list[dict[str, Any]]:
    """Return a bare JSON array of builder-fee event rows (UI contract)."""

    stmt = select(BuilderFeeEvent)
    if market_id:
        stmt = stmt.where(BuilderFeeEvent.market_id == market_id)
    if translator_address:
        stmt = stmt.where(
            BuilderFeeEvent.translator_address == translator_address
        )
    stmt = (
        stmt.order_by(BuilderFeeEvent.timestamp.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = session.exec(stmt).all()
    return [
        {
            "id": r.id,
            "market_id": r.market_id,
            "fill_amount": r.fill_amount,
            "fee_amount": r.fee_amount,
            "translator_address": r.translator_address,
            "arc_tx_hash": r.arc_tx_hash,
            "is_simulated": bool(r.is_simulated),
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        }
        for r in rows
    ]
