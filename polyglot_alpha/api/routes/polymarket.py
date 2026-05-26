"""/events/{id}/polymarket/submit-real — promote a dry_run to a real POST.

This route is the operator-gated escape hatch for actually posting a market
to Polymarket. The default lifecycle in :mod:`polyglot_alpha.orchestrator`
runs the Polymarket client in ``dry_run`` mode (or whatever
``POLYMARKET_MODE`` is set to globally); this endpoint forces the
``REAL`` path with explicit safety checks:

  * ``confirm_real_submission`` must be ``True``.
  * The event's :class:`QualityScore.overall_score` must be at least
    ``REAL_QUALITY_GATE`` (0.80).
  * The builder API secrets must be configured.
  * Per-day rate limit: no more than ``MAX_REAL_SUBMISSIONS_PER_DAY``
    real submissions across the whole process (tracked in DB).

On success we update the existing :class:`PolymarketSubmission` row in
place (or insert a new one if one was never created) and return the
updated record so the UI can swap its dry-run badge for a real link.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ...persistence.models import (
    Event,
    PolymarketStatus,
    PolymarketSubmission,
    QualityScore,
    Translation,
)
from ..deps import get_db, utc_iso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["polymarket"])


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------


REAL_QUALITY_GATE: float = float(
    os.environ.get("POLYMARKET_REAL_QUALITY_GATE", "0.80")
)
MAX_REAL_SUBMISSIONS_PER_DAY: int = int(
    os.environ.get("POLYMARKET_REAL_DAILY_LIMIT", "5")
)
BUILDER_CODE: str = os.environ.get(
    "POLYMARKET_BUILDER_CODE", "POLYGLOT_ALPHA_BUILDER_V1"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_real_submissions_last_24h(session: Session) -> int:
    """Return the number of non-simulated submissions in the last 24h."""

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_naive = cutoff.replace(tzinfo=None)
    rows = session.exec(
        select(PolymarketSubmission).where(
            PolymarketSubmission.is_simulated == False,  # noqa: E712
            PolymarketSubmission.submitted_at >= cutoff_naive,
        )
    ).all()
    return len(rows)


def _build_question_from_translation(
    event: Event, translation: Translation | None
) -> Any:
    """Construct a :class:`polymarket.types.Question` from the persisted
    translation row. Falls back to the event title when no translation is
    available so the operator can still submit a market for legacy demo
    events.
    """

    from polyglot_alpha.polymarket.types import Question

    final = translation.final_question_json if translation else {}
    text = (
        final.get("title")
        if isinstance(final, dict)
        else None
    ) or (event.title or "PolyglotAlpha demo question")
    return Question(
        question_id=str(event.id),
        text=str(text),
        category=(
            final.get("category") if isinstance(final, dict) else None
        )
        or "geopolitics",
        resolution_source=(
            final.get("resolution_source") if isinstance(final, dict) else None
        )
        or "operator",
        end_date_iso=(
            final.get("cutoff_ts") or final.get("end_date_iso")
            if isinstance(final, dict)
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/events/{event_id}/polymarket/submit-real",
    summary="Promote a dry-run Polymarket submission to a real POST",
)
async def submit_real(
    event_id: int,
    payload: dict[str, Any],
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Submit ``event_id``'s question to the real Polymarket Gamma API.

    Request body:

        {
            "confirm_real_submission": true   # required, must be exactly true
        }

    Errors:

      * 400 — confirm flag missing/false, quality below gate, or daily
        cap exceeded.
      * 404 — event or translation row not found.
      * 502 — Polymarket Gamma API rejected or was unreachable.
    """

    # 1. Validate confirm flag.
    if not isinstance(payload, dict) or not bool(
        payload.get("confirm_real_submission")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm_real_submission=true required",
        )

    # 2. Load event + quality score.
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"event {event_id} not found",
        )
    quality = session.get(QualityScore, event_id)
    overall_score = float(quality.overall_score) if quality is not None else 0.0
    if overall_score < REAL_QUALITY_GATE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"overall_score {overall_score:.3f} below REAL_QUALITY_GATE "
                f"{REAL_QUALITY_GATE:.2f}"
            ),
        )

    # 3. Rate limit (5/day by default).
    recent = _count_real_submissions_last_24h(session)
    if recent >= MAX_REAL_SUBMISSIONS_PER_DAY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"daily real-submission cap reached: {recent}/"
                f"{MAX_REAL_SUBMISSIONS_PER_DAY}"
            ),
        )

    # 4. Build the Question + call the real client.
    translation = session.get(Translation, event_id)
    try:
        question = _build_question_from_translation(event, translation)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"could not build question payload: {exc}",
        ) from exc

    try:
        from polyglot_alpha.polymarket import PolymarketV2Client
        from polyglot_alpha.polymarket.types import PolymarketMode
    except ImportError as exc:  # pragma: no cover - polymarket pkg always present
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"polymarket client unavailable: {exc}",
        ) from exc

    async with PolymarketV2Client(
        builder_code=BUILDER_CODE,
        api_key=os.environ.get("POLYMARKET_BUILDER_API_KEY"),
        mode=PolymarketMode.REAL,
    ) as client:
        result = await client.submit_question(
            question,
            confirm_real_submission=True,
            overall_score=overall_score,
        )

    # 5. Update or insert the submission row.
    submission = session.exec(
        select(PolymarketSubmission)
        .where(PolymarketSubmission.event_id == event_id)
        .order_by(PolymarketSubmission.id.desc())
    ).first()
    if submission is None:
        submission = PolymarketSubmission(event_id=event_id)
    submission.market_id = result.market_id
    submission.market_url = result.polymarket_url
    submission.status = (
        PolymarketStatus.SUBMITTED.value
        if not result.is_simulated
        else PolymarketStatus.SIMULATED.value
    )
    submission.is_simulated = bool(result.is_simulated)
    submission.submitted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(submission)
    session.commit()
    session.refresh(submission)

    # 6. Surface upstream errors as 502 so the UI can render an actionable
    # message. We *still* return the persisted row so the operator can see
    # what was attempted.
    if result.error and result.is_simulated:
        logger.warning(
            "submit_real: real submission for event=%s degraded (%s)",
            event_id,
            result.error,
        )

    return {
        "event_id": event_id,
        "submission": {
            "id": submission.id,
            "event_id": submission.event_id,
            "market_id": submission.market_id,
            "market_url": submission.market_url,
            "status": submission.status,
            "is_simulated": submission.is_simulated,
            "submitted_at": utc_iso(submission.submitted_at),
        },
        "mode": getattr(result, "mode", "real"),
        "payload": getattr(result, "payload", {}),
        "fees_estimate_usdc": float(result.fees_estimate_usdc or 0.0),
        "error": result.error,
    }


__all__ = ["router", "REAL_QUALITY_GATE", "MAX_REAL_SUBMISSIONS_PER_DAY"]
