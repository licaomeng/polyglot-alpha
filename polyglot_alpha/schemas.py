"""Pydantic schemas shared across the pipeline.

These are intentionally minimal: only the fields the agent code actually
consumes/produces are typed here. The full upstream pipeline (analysts,
translators, synthesizer) adds richer fields at runtime, but agents only
need a stable subset.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class NewsEvent(BaseModel):
    """A Chinese-language news event waiting to be translated into a
    Polymarket-shaped market question."""

    model_config = ConfigDict(extra="allow")

    event_id: str
    url: str
    title_zh: str
    body_zh: str
    cutoff_ts: int = Field(..., description="Unix timestamp of news cutoff.")
    topic: Optional[str] = None
    source: Optional[str] = None


class AnalystReport(BaseModel):
    """Analyst output: a short structured take on an event."""

    model_config = ConfigDict(extra="allow")

    analyst_id: str
    summary: str
    relevant_entities: List[str] = Field(default_factory=list)
    risk_factors: List[str] = Field(default_factory=list)


class TranslationCandidate(BaseModel):
    """One translator's proposed market question."""

    model_config = ConfigDict(extra="allow")

    translator_id: str
    question_en: str
    resolution_criteria: str
    end_date_iso: str
    tags: List[str] = Field(default_factory=list)


class Question(BaseModel):
    """Final synthesized Polymarket-shaped market question."""

    model_config = ConfigDict(extra="allow")

    event_id: str
    question_en: str
    resolution_criteria: str
    end_date_iso: str
    yes_outcome: str = "YES"
    no_outcome: str = "NO"
    confidence: float = 0.0
    quality_score: float = 0.0


class EvaluationResult(BaseModel):
    """A translator agent's pre-bid self-evaluation of an event."""

    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(..., ge=0.0, le=1.0)
    expected_cost_usdc: float = Field(..., ge=0.0)
    estimated_quality: float = Field(..., ge=0.0, le=1.0)
    bid_amount_usdc: float = Field(..., ge=0.0)


class QualityScore(BaseModel):
    model_config = ConfigDict(extra="allow")

    score: float = Field(..., ge=0.0, le=1.0)
    rationale: str = ""
    passed: bool = False


def event_dict_to_model(event: Dict[str, Any]) -> NewsEvent:
    """Coerce a loose dict into a NewsEvent, supplying safe defaults for the
    optional fields. Used by agents that receive raw chain-event payloads."""

    return NewsEvent(
        event_id=str(event.get("event_id") or event.get("eventId") or ""),
        url=str(event.get("url") or ""),
        title_zh=str(event.get("title_zh") or event.get("title") or ""),
        body_zh=str(event.get("body_zh") or event.get("body") or ""),
        cutoff_ts=int(event.get("cutoff_ts") or event.get("cutoff") or 0),
        topic=event.get("topic"),
        source=event.get("source"),
    )
