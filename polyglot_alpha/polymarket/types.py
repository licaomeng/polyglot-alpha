"""Pydantic models shared across the polymarket subpackage.

Keeping types in a tiny module avoids circular imports between
client / mock_client / fill_listener while still giving us a single
source of truth for the wire shapes.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PolymarketMode(str, Enum):
    """Which Polymarket implementation to talk to.

    * ``REAL``    — hits the live Polymarket Gamma/CLOB REST endpoints.
                    Requires builder API key/secret/passphrase + an explicit
                    per-call ``confirm_real_submission=True`` flag (set by
                    the operator) to prevent accidental production traffic.
    * ``DRY_RUN`` — constructs the full real-shape Gamma payload, logs it,
                    but never POSTs it. Default for the hackathon demo so
                    judges can inspect what *would* have been submitted.
    * ``MOCK``    — deterministic synthetic submission used in unit tests
                    and offline CI.
    """

    REAL = "real"
    DRY_RUN = "dry_run"
    MOCK = "mock"


class Question(BaseModel):
    """Minimal question shape the orchestrator hands us after judge=PASS.

    We deliberately keep this decoupled from any DB model; callers can
    construct it from their own ORM row or dict.
    """

    question_id: str
    text: str
    category: Optional[str] = None
    resolution_source: Optional[str] = None
    end_date_iso: Optional[str] = None
    initial_liquidity_usdc: float = 100.0


class SubmissionResult(BaseModel):
    """Outcome of submitting a question to Polymarket."""

    market_id: str
    polymarket_url: str
    status: str  # "submitted" | "pending" | "failed" | "dry_run"
    fees_estimate_usdc: float = 0.0
    is_simulated: bool = False
    error: Optional[str] = None
    # ``mode`` mirrors the :class:`PolymarketMode` the submission was
    # produced in. Defaults to ``"unknown"`` so legacy callers that did
    # not set the field continue to deserialize correctly.
    mode: str = "unknown"
    # ``payload`` is the exact Gamma-API request body the client built —
    # populated in dry-run and real modes so callers can show "what was
    # (or would have been) sent" in the UI. Empty in mock mode.
    payload: dict = Field(default_factory=dict)


class Fill(BaseModel):
    """A single Polymarket fill event credited to our builder code."""

    fill_id: str
    market_id: str
    fill_amount_usdc: float
    builder_fee_usdc: float
    timestamp: int  # unix seconds
    taker_address: Optional[str] = None
    is_simulated: bool = False


class BuilderFeeEvent(BaseModel):
    """Persisted row mirror — what FillListener writes to ``builder_fee_events``.

    The matching ``polymarket_submissions`` row carries its own
    ``is_simulated`` flag; this one mirrors it so a downstream UI can
    label individual fill events honestly even when joined out of order.
    """

    fill_id: str
    market_id: str
    translator_address: str
    fill_amount_usdc: float = Field(ge=0)
    builder_fee_usdc: float = Field(ge=0)
    tx_hash: Optional[str] = None
    on_chain_status: str = "pending"  # "pending" | "confirmed" | "failed"
    is_simulated: bool = False
    timestamp: int = 0
