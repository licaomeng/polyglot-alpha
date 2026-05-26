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
from ..deps import get_db, utc_iso as _utc_iso

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
    # ``mode`` is the W5 lifecycle mode (``"live"`` | ``"mock"``). Older
    # rows pre-W5-A1 have NULL — the DB migration backfills them with
    # ``'live'`` but we also guard here defensively for any path that
    # bypassed the migration.
    event_mode = getattr(event, "mode", None) or "live"
    return {
        "id": str(event.id) if event.id is not None else None,
        "content_hash": event.content_hash,
        "sources": event.sources,
        "source": source_name,
        "language": event.language,
        "title": event.title,
        "headline": event.title,
        "triggered_at": _utc_iso(event.triggered_at),
        "ingestedAt": _utc_iso(event.triggered_at),
        "status": event.status,
        "mode": event_mode,
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
    auction_diagnostics: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Synthesize 7 phase records reflecting on-chain progress for the UI."""

    # Event Ingestion is RUNNING while status=PENDING (RSS poll + Haiku
    # scoring still in flight); only flip to completed once status has moved
    # past PENDING to AUCTION_OPEN or beyond. This makes phase 0 light up
    # then settle as the user watches, instead of being pre-completed at
    # page-load time.
    ingestion_completed = event.status not in ("PENDING",)
    completed_flags = [
        ingestion_completed,
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
            # Auction diagnostics are merged below when available so the UI
            # can render an actionable panel on low-gas / partial-auction.
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

    # Merge auction diagnostics (low-gas / partial-auction) onto phase 2.
    # ``auction_diagnostics`` is populated by ``_drive_real_auction`` when
    # one or more seeder wallets were skipped pre-flight.
    if auction_diagnostics:
        details[1].update(
            {
                "partial_auction": bool(
                    auction_diagnostics.get("partial_auction")
                ),
                "skipped_bidders": list(
                    auction_diagnostics.get("skipped_bidders", []) or []
                ),
                "skip_reasons": dict(
                    auction_diagnostics.get("skip_reasons", {}) or {}
                ),
                "balances_eth": dict(
                    auction_diagnostics.get("balances_eth", {}) or {}
                ),
                "threshold_eth": auction_diagnostics.get("threshold_eth"),
            }
        )
        if auction_diagnostics.get("all_seeders_low_gas"):
            details[1]["reason"] = "all_seeders_low_gas"

    timestamps: list[Optional[str]] = [
        _utc_iso(event.triggered_at),
        _utc_iso(auction.settled_at) if auction is not None else None,
        _utc_iso(translation.completed_at) if translation is not None else None,
        _utc_iso(quality.evaluated_at) if quality is not None else None,
        _utc_iso(question.committed_at) if question is not None else None,
        _utc_iso(submission.submitted_at) if submission is not None else None,
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


# Judge weight + display metadata. Mirrors ``polyglot_alpha.judges.panel._WEIGHTS``
# but kept duplicated here so the API never imports the closed-IP panel module
# at request time. Score thresholds are conservative defaults used purely for
# the backfill fallback when ``_judges`` is missing.
_TRANSLATION_JUDGE_NAMES: tuple[str, ...] = ("bleu", "comet", "mqm_llm")
_STYLE_JUDGE_NAMES: tuple[tuple[str, str], ...] = (
    ("d1", "d1_structural"),
    ("d2", "d2_stylistic"),
    ("d3", "d3_framing"),
    ("d4", "d4_granularity"),
    ("d5", "d5_resolution_clarity"),
    ("d6", "d6_source_reliability"),
    ("d7", "d7_leading_check"),
    ("d8", "d8_duplicate_detection"),
)


def _synthesize_mock_panel_dossier(
    translation_scores: dict[str, Any],
    style_alignment_passes: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Build an 11-row dossier for the legacy mock-verdict shape.

    The orchestrator's offline fallback writes ``judge_N`` translation scores
    + ``style_judge_N`` style passes. We map them positionally onto the real
    11 judge slot names so the UI shows a consistent breakdown.
    """

    dossier: list[dict[str, Any]] = []
    translation_keys_in_order = sorted(
        (k for k in translation_scores.keys() if k.startswith("judge_")),
        key=lambda k: int(k.split("_")[-1]) if k.split("_")[-1].isdigit() else 0,
    )
    style_keys_in_order = sorted(
        (k for k in style_alignment_passes.keys() if k.startswith("style_judge_")),
        key=lambda k: int(k.split("_")[-1]) if k.split("_")[-1].isdigit() else 0,
    )

    # Map the first 3 ``judge_N`` slots to the canonical translation judges
    # so the UI shows "bleu / comet / mqm_llm" instead of opaque ordinals.
    for idx, slot in enumerate(_TRANSLATION_JUDGE_NAMES):
        if idx < len(translation_keys_in_order):
            raw = translation_scores.get(translation_keys_in_order[idx])
            score = float(raw) if isinstance(raw, (int, float)) else 0.0
            dossier.append(
                {
                    "name": slot,
                    "passed": score >= 0.7,
                    "score": score,
                    "reason": (
                        f"Mock panel · {translation_keys_in_order[idx]}={score:.2f}"
                    ),
                    "panelBudgetExceeded": False,
                    "softSkip": False,
                    "timeout": False,
                    "panelPartial": False,
                }
            )
        else:
            dossier.append(
                {
                    "name": slot,
                    "passed": True,
                    "score": 0.85,
                    "reason": "Mock panel · synthesized default",
                    "panelBudgetExceeded": False,
                    "softSkip": True,
                    "timeout": False,
                    "panelPartial": False,
                }
            )

    # Remaining ``judge_N`` slots + ``style_judge_N`` map onto the 8 style
    # judges (D1-D8). Use the style booleans where available, fall back to
    # the mock 0.85 PASS sentinel otherwise.
    for idx, (short, full) in enumerate(_STYLE_JUDGE_NAMES):
        passed = True
        score = 0.85
        reason = "Mock panel · style PASS"
        if idx < len(style_keys_in_order):
            raw_pass = style_alignment_passes.get(style_keys_in_order[idx])
            passed = bool(raw_pass)
            score = 1.0 if passed else 0.0
            reason = (
                f"Mock panel · {style_keys_in_order[idx]}="
                f"{'pass' if passed else 'fail'} (mapped to {short.upper()})"
            )
        dossier.append(
            {
                "name": full,
                "passed": passed,
                "score": score,
                "reason": reason,
                "panelBudgetExceeded": False,
                "softSkip": False,
                "timeout": False,
                "panelPartial": False,
            }
        )

    return dossier, False


def _synthesize_judges_from_quality(
    translation_scores: dict[str, Any],
    style_alignment_passes: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Reconstruct an 11-row judge dossier from the legacy quality columns.

    Returns ``(dossier, derived_panel_partial)`` where ``derived_panel_partial``
    is ``True`` when any judge looks like it carries an INSUFFICIENT_DATA
    sentinel (``null`` translation score or missing style pass), so the UI
    can still surface the "Partial" header for events evaluated before the
    panel started emitting an explicit ``_panelPartial`` flag.
    """

    # The orchestrator's mock-verdict fallback stores ``judge_N`` and
    # ``style_judge_N`` keys instead of the real-panel ``bleu/comet/mqm`` +
    # ``d1..d8`` shape. Detect that and short-circuit with a synthetic
    # "MOCK PANEL" dossier so the UI doesn't show 11 INSUFFICIENT_DATA rows
    # for events that were actually evaluated successfully via the mock path.
    is_mock_panel = (
        any(k.startswith("judge_") for k in translation_scores.keys())
        and not any(k in translation_scores for k in ("bleu", "comet", "mqm"))
    )
    if is_mock_panel:
        return _synthesize_mock_panel_dossier(
            translation_scores, style_alignment_passes
        )

    dossier: list[dict[str, Any]] = []
    partial = False

    # Translation judges.
    bleu_raw = translation_scores.get("bleu")
    comet_raw = translation_scores.get("comet")
    mqm_raw = translation_scores.get("mqm")
    for name in _TRANSLATION_JUDGE_NAMES:
        if name == "bleu":
            score = float(bleu_raw) if isinstance(bleu_raw, (int, float)) else None
            passed = score is not None and score >= 25.0
            reason = (
                f"BLEU={score:.1f}" if score is not None
                else "BLEU offline / reference unavailable"
            )
            budget_exceeded = score is None
        elif name == "comet":
            score = float(comet_raw) if isinstance(comet_raw, (int, float)) else None
            passed = score is not None and score >= 0.6
            reason = (
                f"COMET={score:.3f}" if score is not None
                else "COMET offline / model unavailable"
            )
            budget_exceeded = score is None
        else:  # mqm_llm
            score_val = None
            major = 0
            if isinstance(mqm_raw, dict):
                if isinstance(mqm_raw.get("score"), (int, float)):
                    score_val = float(mqm_raw["score"])
                if isinstance(mqm_raw.get("major_count"), (int, float)):
                    major = int(mqm_raw["major_count"])
            passed = score_val is not None and score_val >= 80 and major == 0
            reason = (
                f"MQM score={score_val:.0f}, majors={major}"
                if score_val is not None
                else "MQM offline / LLM unavailable"
            )
            budget_exceeded = score_val is None
            score = (score_val / 100.0) if score_val is not None else None

        if budget_exceeded:
            partial = True
        dossier.append(
            {
                "name": name,
                "passed": bool(passed),
                "score": float(score) if isinstance(score, (int, float)) else 0.0,
                "reason": reason,
                "panelBudgetExceeded": budget_exceeded,
                "softSkip": budget_exceeded and name in ("bleu", "comet"),
                "timeout": False,
                "panelPartial": budget_exceeded,
            }
        )

    # Style judges (D1-D8).
    for short, full in _STYLE_JUDGE_NAMES:
        raw_pass = style_alignment_passes.get(short)
        if raw_pass is None:
            partial = True
            dossier.append(
                {
                    "name": full,
                    "passed": False,
                    "score": 0.0,
                    "reason": (
                        f"INSUFFICIENT_DATA: {short} verdict missing from "
                        "stored quality_scores row"
                    ),
                    "panelBudgetExceeded": True,
                    "softSkip": short == "d8",
                    "timeout": False,
                    "panelPartial": True,
                }
            )
        else:
            passed = bool(raw_pass)
            dossier.append(
                {
                    "name": full,
                    "passed": passed,
                    "score": 1.0 if passed else 0.0,
                    "reason": (
                        f"{short.upper()} {'passed' if passed else 'failed'} style gate"
                    ),
                    "panelBudgetExceeded": False,
                    "softSkip": False,
                    "timeout": False,
                    "panelPartial": False,
                }
            )

    return dossier, partial


def _serialize_event_detail(
    session: Session, event: Event
) -> dict[str, Any]:
    """Build the rich event detail object expected by the UI."""

    # Best-effort auction diagnostics (process-local; populated by the
    # orchestrator when one or more seeders were skipped pre-flight).
    auction_diagnostics: Optional[dict[str, Any]] = None
    try:
        from ...orchestrator import get_auction_diagnostics

        if event.id is not None:
            auction_diagnostics = get_auction_diagnostics(event.id)
    except Exception:  # pragma: no cover - defensive: never break /events
        auction_diagnostics = None

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

    fee_rows: list[BuilderFeeEvent] = []
    has_fee_event = False
    if submission is not None and submission.market_id:
        fee_rows = list(
            session.exec(
                select(BuilderFeeEvent)
                .where(BuilderFeeEvent.market_id == submission.market_id)
                .order_by(BuilderFeeEvent.timestamp.asc())
            ).all()
        )
        has_fee_event = len(fee_rows) > 0

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

    # Rich Polymarket submission block — UI consumes ``event.polymarket.*``
    # via optional-chaining; emit ``None`` when the pipeline hasn't reached
    # the submission phase yet so consumers can render an empty state.
    polymarket_block: Optional[dict[str, Any]] = None
    if submission is not None:
        polymarket_block = {
            "marketId": submission.market_id,
            "marketUrl": submission.market_url,
            "isSimulated": bool(submission.is_simulated),
            "mode": submission.mode,
            "status": submission.status,
            "builderCode": getattr(question, "builder_code", None),
            "payload": submission.payload,
            "feesEstimateUsdc": submission.fees_estimate_usdc,
            "submittedAt": _utc_iso(submission.submitted_at),
            "revenueStream": [
                {
                    "recipient": r.translator_address,
                    "usd": r.fee_amount,
                    "fillAmount": r.fill_amount,
                    "arcTxHash": r.arc_tx_hash,
                    "isSimulated": bool(r.is_simulated),
                    "ts": _utc_iso(r.timestamp),
                }
                for r in fee_rows
            ],
        }

    # Extract the per-judge dossier the panel smuggles through the
    # ``translation_scores`` JSON column under the ``_judges`` underscore-
    # prefixed key. We surface it as top-level ``judges`` + ``panelPartial``
    # so the UI can render the 11-judge breakdown without parsing the
    # private side-channel.
    translation_scores_raw: dict[str, Any] = (
        dict(quality.translation_scores) if quality is not None else {}
    )
    judges_dossier: list[dict[str, Any]] = []
    panel_partial: bool = False
    pending_judge_names: list[str] = []
    if isinstance(translation_scores_raw, dict):
        raw_judges = translation_scores_raw.get("_judges")
        if isinstance(raw_judges, list):
            judges_dossier = [dict(j) for j in raw_judges if isinstance(j, dict)]
        panel_partial = bool(translation_scores_raw.get("_panelPartial"))
        raw_pending = translation_scores_raw.get("_pendingJudgeNames")
        if isinstance(raw_pending, list):
            pending_judge_names = [str(n) for n in raw_pending]
    # Backfill: events evaluated before the panel started emitting ``_judges``
    # have ``translation_scores`` + ``style_alignment_passes`` populated but no
    # dossier. Synthesize an 11-row dossier from those existing columns so the
    # UI can still render per-judge passes for historical events.
    if (
        quality is not None
        and not judges_dossier
        and isinstance(translation_scores_raw, dict)
    ):
        judges_dossier, derived_partial = _synthesize_judges_from_quality(
            translation_scores_raw,
            quality.style_alignment_passes
            if isinstance(quality.style_alignment_passes, dict)
            else {},
        )
        if derived_partial and not panel_partial:
            panel_partial = True
        if not pending_judge_names:
            pending_judge_names = [
                str(j["name"])
                for j in judges_dossier
                if j.get("panelBudgetExceeded")
            ]
    # Strip the underscore-prefixed metadata before returning so the public
    # ``translation_scores`` payload stays the original {bleu, comet, mqm}
    # shape consumers already depend on.
    translation_scores_public: Optional[dict[str, Any]] = None
    if quality is not None:
        translation_scores_public = {
            k: v
            for k, v in translation_scores_raw.items()
            if not (isinstance(k, str) and k.startswith("_"))
        }
    # W9-A: surface the on-chain 11-judge attestation (γ-strategy) as a
    # top-level ``judgesAttestation`` field. The orchestrator stashes it
    # in ``translation_scores._judgesAttestation`` so persistence works
    # without a schema migration; the API hoists it out of the private
    # underscore namespace so the UI can render an arcscan link.
    judges_attestation_public: Optional[dict[str, Any]] = None
    if isinstance(translation_scores_raw, dict):
        raw_attest = translation_scores_raw.get("_judgesAttestation")
        if isinstance(raw_attest, dict):
            judges_attestation_public = {
                "txHash": raw_attest.get("txHash"),
                "attestationHash": raw_attest.get("attestationHash"),
                "scoreScaled": raw_attest.get("scoreScaled"),
                "aggregatorAddress": raw_attest.get("aggregatorAddress"),
                "registerTx": raw_attest.get("registerTx"),
                "strategy": raw_attest.get("strategy", "gamma_aggregate"),
            }

    detail: dict[str, Any] = _serialize_event_summary(event)
    detail.update(
        {
            "winner_address": getattr(auction, "winner_address", None),
            "winning_bid": getattr(auction, "winning_bid", None),
            "verdict": getattr(quality, "verdict", None),
            "overall_score": getattr(quality, "overall_score", None),
            "anchor": anchor,
            "translation_scores": translation_scores_public,
            "style_alignment_passes": (
                quality.style_alignment_passes if quality is not None else None
            ),
            "judges": judges_dossier,
            "judgesAttestation": judges_attestation_public,
            "panelPartial": panel_partial,
            "pendingJudgeNames": pending_judge_names,
            "question_id": getattr(question, "question_id_onchain", None),
            "builder_code": getattr(question, "builder_code", None),
            "market_id": getattr(submission, "market_id", None),
            "market_url": getattr(submission, "market_url", None),
            "is_simulated": (
                bool(submission.is_simulated) if submission is not None else None
            ),
            "polymarket": polymarket_block,
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
                auction_diagnostics,
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
                    "submitted_at": _utc_iso(b.submitted_at),
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

    # Top-level failure surface for the UI. Only populate when we have
    # diagnostics, so the happy path stays untouched.
    if auction_diagnostics:
        all_low = bool(auction_diagnostics.get("all_seeders_low_gas"))
        detail["auction_diagnostics"] = auction_diagnostics
        if event.status == EventStatus.FAILED.value and all_low:
            detail["reason"] = "all_seeders_low_gas"
            detail["failure_details"] = {
                "skipped_bidders": auction_diagnostics.get(
                    "skipped_bidders", []
                ),
                "skip_reasons": auction_diagnostics.get("skip_reasons", {}),
                "balances_eth": auction_diagnostics.get("balances_eth", {}),
                "threshold_eth": auction_diagnostics.get("threshold_eth"),
            }

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
                "submitted_at": _utc_iso(b.submitted_at),
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
            "completed_at": _utc_iso(t.completed_at),
        }
        for t in translations
    ]
