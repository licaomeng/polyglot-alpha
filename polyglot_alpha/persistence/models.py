"""SQLModel tables for PolyglotAlpha v2 (README §5.35 — 10 base tables + 5
corpus/ground-truth/backtest tables for T7/T9 ingestion pipeline).

All tables use JSON columns via SQLAlchemy's JSON type, so they work with
both SQLite (default) and PostgreSQL (DATABASE_URL override).
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import CheckConstraint, Column, Index, JSON
from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# Enums (stored as plain strings for cross-DB portability)
# ---------------------------------------------------------------------------


class EventStatus(str, enum.Enum):
    PENDING = "PENDING"
    AUCTION_OPEN = "AUCTION_OPEN"
    AUCTION_SETTLED = "AUCTION_SETTLED"
    TRANSLATING = "TRANSLATING"
    EVALUATING = "EVALUATING"
    REJECTED = "REJECTED"
    COMMITTED = "COMMITTED"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"


class JudgeVerdict(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"


class PolymarketStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    LIVE = "LIVE"
    FAILED = "FAILED"
    SIMULATED = "SIMULATED"


class SourceStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. events
# ---------------------------------------------------------------------------


class Event(SQLModel, table=True):
    __tablename__ = "events"

    id: Optional[int] = Field(default=None, primary_key=True)
    content_hash: str = Field(index=True, unique=True)
    sources: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    language: str = Field(default="en", index=True)
    triggered_at: datetime = Field(default_factory=_utcnow, index=True)
    status: str = Field(default=EventStatus.PENDING.value, index=True)
    title: Optional[str] = None
    # Set by the legacy ingestion dispatcher when it calls
    # ``TranslationAuction.openAuction``. The orchestrator stores its own
    # auction tx hashes in :class:`Auction.settlement_tx_hash`.
    tx_hash: Optional[str] = None


# ---------------------------------------------------------------------------
# 2. bids
# ---------------------------------------------------------------------------


class Bid(SQLModel, table=True):
    __tablename__ = "bids"
    __table_args__ = (
        CheckConstraint(
            "bid_amount > 0 AND bid_amount < 1000000",
            name="bid_amount_positive_sane",
        ),
        CheckConstraint(
            "length(agent_address) > 0",
            name="agent_address_nonempty",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="events.id", index=True)
    agent_address: str = Field(index=True)
    bid_amount: float
    stake_amount: float = 5.0
    candidate_hash: Optional[str] = None
    tx_hash: Optional[str] = None
    # Reputation snapshot at bid-time (0-1). Populated from BidRecord so the
    # historical view of "what reputation did this bidder have when they
    # bid?" survives even if AgentReputation rolls forward later.
    reputation: float = 1.0
    submitted_at: datetime = Field(default_factory=_utcnow, index=True)


# ---------------------------------------------------------------------------
# 3. auctions
# ---------------------------------------------------------------------------


class Auction(SQLModel, table=True):
    __tablename__ = "auctions"

    event_id: int = Field(foreign_key="events.id", primary_key=True)
    winner_address: Optional[str] = Field(default=None, index=True)
    winning_bid: Optional[float] = None
    settlement_tx_hash: Optional[str] = None
    settled_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# 4. translations
# ---------------------------------------------------------------------------


class Translation(SQLModel, table=True):
    __tablename__ = "translations"

    event_id: int = Field(foreign_key="events.id", primary_key=True)
    translator_address: str = Field(index=True)
    pipeline_trace_ipfs: Optional[str] = None
    final_question_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    completed_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# 5. quality_scores
# ---------------------------------------------------------------------------


class QualityScore(SQLModel, table=True):
    __tablename__ = "quality_scores"
    __table_args__ = (
        CheckConstraint(
            "overall_score >= 0 AND overall_score <= 1",
            name="overall_score_unit",
        ),
        CheckConstraint(
            "verdict IN ('PASS', 'FAIL', 'PENDING', 'BORDERLINE')",
            name="verdict_enum",
        ),
    )

    event_id: int = Field(foreign_key="events.id", primary_key=True)
    translation_scores: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    style_alignment_passes: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    overall_score: float = 0.0
    verdict: str = Field(default=JudgeVerdict.PENDING.value, index=True)
    evaluated_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# 6. questions
# ---------------------------------------------------------------------------


class Question(SQLModel, table=True):
    __tablename__ = "questions"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="events.id", index=True)
    question_id_onchain: Optional[str] = Field(default=None, index=True)
    title_hash: Optional[str] = None
    builder_code: Optional[str] = None
    reasoning_ipfs: Optional[str] = None
    committed_at: datetime = Field(default_factory=_utcnow)
    # Arc commit transaction hash returned by QuestionRegistry.commitQuestion
    # (or the deterministic mock hash in mock mode). Surfaced by the UI on
    # the On-chain Anchor phase as a TxLink to testnet.arcscan.app.
    tx_hash: Optional[str] = None


# ---------------------------------------------------------------------------
# 7. polymarket_submissions
# ---------------------------------------------------------------------------


class PolymarketSubmission(SQLModel, table=True):
    __tablename__ = "polymarket_submissions"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="events.id", index=True)
    market_id: Optional[str] = Field(default=None, index=True)
    submitted_at: datetime = Field(default_factory=_utcnow)
    market_url: Optional[str] = None
    status: str = Field(default=PolymarketStatus.PENDING.value, index=True)
    is_simulated: bool = False
    # Rich submission metadata so the UI can surface what was actually
    # sent to the Polymarket V2 builder API (and what the response /
    # builder-fee linkage looks like). Added 2026-05-26 — backfilled
    # with NULL on existing rows by ``_migrate_polymarket_submissions``.
    mode: Optional[str] = Field(default=None)
    fees_estimate_usdc: Optional[float] = Field(default=None)
    payload: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))


# ---------------------------------------------------------------------------
# 8. builder_fee_events
# ---------------------------------------------------------------------------


class BuilderFeeEvent(SQLModel, table=True):
    __tablename__ = "builder_fee_events"
    __table_args__ = (
        CheckConstraint("fill_amount >= 0", name="fill_nonneg"),
        CheckConstraint(
            "fee_amount >= 0 AND fee_amount <= fill_amount",
            name="fee_within_fill",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    market_id: str = Field(index=True)
    fill_amount: float
    fee_amount: float
    translator_address: str = Field(index=True)
    arc_tx_hash: Optional[str] = None
    timestamp: datetime = Field(default_factory=_utcnow, index=True)
    is_simulated: bool = False


# ---------------------------------------------------------------------------
# 9. agent_reputation
# ---------------------------------------------------------------------------


class AgentReputation(SQLModel, table=True):
    __tablename__ = "agent_reputation"
    __table_args__ = (
        CheckConstraint("total_wins <= total_bids", name="wins_le_bids"),
        CheckConstraint("cumulative_fees >= 0", name="fees_nonneg"),
        CheckConstraint(
            "avg_quality >= 0 AND avg_quality <= 1",
            name="avg_quality_unit",
        ),
        # Hot-path leaderboard query sorts by cumulative_fees DESC.
        Index(
            "ix_agent_reputation_cumulative_fees_desc",
            "cumulative_fees",
            postgresql_using="btree",
        ),
    )

    agent_address: str = Field(primary_key=True)
    total_bids: int = 0
    total_wins: int = 0
    avg_quality: float = 0.0
    cumulative_fees: float = 0.0
    last_updated: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# 10. sources
# ---------------------------------------------------------------------------


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    url: str
    # ``language`` + ``status`` previously had per-column indexes that were
    # never queried — dropped to reduce write overhead (DB integrity report).
    language: str = "en"
    last_fetched: Optional[datetime] = None
    status: str = SourceStatus.ACTIVE.value


# ---------------------------------------------------------------------------
# Corpus / ground-truth / backtest tables (extension for T7/T9 ingestion).
# ---------------------------------------------------------------------------


class FewShotRole(str, enum.Enum):
    POSITIVE_EXAMPLE = "POSITIVE_EXAMPLE"
    NEGATIVE_EXAMPLE = "NEGATIVE_EXAMPLE"
    EDGE_CASE = "EDGE_CASE"


class CorpusMarketState(str, enum.Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"
    DISPUTED = "disputed"
    ARCHIVED = "archived"


# ---------------------------------------------------------------------------
# 11. corpus_markets
# ---------------------------------------------------------------------------


class CorpusMarket(SQLModel, table=True):
    """All Polymarket markets (open + resolved) — source-of-truth corpus."""

    __tablename__ = "corpus_markets"
    __table_args__ = (
        CheckConstraint(
            "state != 'resolved' OR outcome IS NOT NULL",
            name="resolved_has_outcome",
        ),
        CheckConstraint(
            "end_date IS NULL OR created_at IS NULL OR end_date >= created_at",
            name="time_order",
        ),
    )

    market_id: str = Field(primary_key=True)
    question: str
    category: Optional[str] = Field(default=None, index=True)
    subcategory: Optional[str] = None
    tags: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    created_at: Optional[datetime] = None
    end_date: Optional[datetime] = None
    resolved_at: Optional[datetime] = Field(default=None, index=True)
    state: str = Field(default=CorpusMarketState.ACTIVE.value, index=True)
    outcome: Optional[str] = Field(default=None, index=True)
    outcome_prices: Optional[list[float]] = Field(default=None, sa_column=Column(JSON))
    total_volume_usdc: Optional[float] = None
    uma_dispute: bool = False
    resolution_source: Optional[str] = None
    is_community_created: bool = False
    embedding_idx: Optional[int] = Field(default=None, index=True)
    framing_pattern: Optional[str] = Field(default=None, index=True)


# ---------------------------------------------------------------------------
# 12. few_shot_exemplars
# ---------------------------------------------------------------------------


class FewShotExemplar(SQLModel, table=True):
    """Exemplars used by LLM judges for in-context learning."""

    __tablename__ = "few_shot_exemplars"

    id: Optional[int] = Field(default=None, primary_key=True)
    market_id: Optional[str] = Field(
        default=None, foreign_key="corpus_markets.market_id", index=True
    )
    judge_dimension: str = Field(index=True)
    role: str = Field(default=FewShotRole.POSITIVE_EXAMPLE.value, index=True)
    question_text: str
    explanation: str
    weight: float = 1.0


# ---------------------------------------------------------------------------
# 13. style_rules
# ---------------------------------------------------------------------------


class StyleRule(SQLModel, table=True):
    """Distilled style-guide bullets — LLM-distilled from corpus."""

    __tablename__ = "style_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    rule_text: str
    dimension: Optional[str] = Field(default=None, index=True)
    source: str = "llm_distilled"
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# 14. reference_translations
# ---------------------------------------------------------------------------


class ReferenceTranslation(SQLModel, table=True):
    """Human-verified reference translations for demo samples (D-judge gold)."""

    __tablename__ = "reference_translations"

    sample_id: int = Field(primary_key=True)
    source_chinese: str
    primary_translation: str
    alternative_phrasings: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    k5_framing_variants: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    expected_bleu_threshold: float = 25.0
    expected_comet_threshold: float = 0.55
    polymarket_shape_validation: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON)
    )
    annotator_notes: Optional[str] = None


# ---------------------------------------------------------------------------
# 15. backtest_results
# ---------------------------------------------------------------------------


class BacktestResult(SQLModel, table=True):
    """Per-event backtest record: agent prediction vs resolved outcome."""

    __tablename__ = "backtest_results"

    id: Optional[int] = Field(default=None, primary_key=True)
    market_id: str = Field(foreign_key="corpus_markets.market_id", index=True)
    agent_address: str = Field(index=True)
    predicted_outcome: Optional[str] = None
    actual_outcome: str
    correct: bool = False
    estimated_profit_usdc: float = 0.0
    # ``judge_verdict`` + ``backtested_at`` previously had per-column indexes
    # that were never queried — dropped to reduce write overhead.
    judge_verdict: str = JudgeVerdict.PENDING.value
    judge_score: float = 0.0
    notes: Optional[str] = None
    backtested_at: datetime = Field(default_factory=_utcnow)


__all__ = [
    "AgentReputation",
    "Auction",
    "BacktestResult",
    "Bid",
    "BuilderFeeEvent",
    "CorpusMarket",
    "CorpusMarketState",
    "Event",
    "EventStatus",
    "FewShotExemplar",
    "FewShotRole",
    "JudgeVerdict",
    "PolymarketStatus",
    "PolymarketSubmission",
    "QualityScore",
    "Question",
    "ReferenceTranslation",
    "Source",
    "SourceStatus",
    "StyleRule",
    "Translation",
]
