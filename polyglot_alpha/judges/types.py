"""Shared dataclasses for the quality panel.

Keeping these out of ``__init__.py`` keeps import cycles tame and lets
individual judges depend on the types without dragging in heavy
dependencies (sacrebleu, comet, sentence-transformers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_BORDERLINE = "BORDERLINE"

BLEU_PASS_THRESHOLD = 25.0
COMET_PASS_THRESHOLD = 0.6
# README §5.22 D8: cosine >= 0.92 -> duplicate (hard reject).
DUPLICATE_COSINE_THRESHOLD = 0.92
# README §5.22: MQM score must be >= 80 for translation gate.
MQM_PASS_THRESHOLD = 80

# Hard gates (must ALL pass for PASS verdict): D1 Structural, D5 Resolution
# Clarity, D8 Duplicate. README §5.22 aggregation rule. D4 was historically
# treated as a hard gate too but the canonical rule treats it as a soft gate.
HARD_STYLE_REQUIREMENTS: tuple[str, ...] = ("d1", "d5", "d8")
# Soft gates: 4 of 5 must pass.
MAJORITY_STYLE_POOL: tuple[str, ...] = ("d2", "d3", "d4", "d6", "d7")
MAJORITY_REQUIRED_COUNT: int = 4


# --------------------------------------------------------------------------- #
# Result types                                                                #
# --------------------------------------------------------------------------- #


@dataclass
class JudgeResult:
    """Outcome of a single judge.

    ``score`` is normalized to the unit interval [0, 1] so the panel
    aggregator can blend translation and style scores without rescaling.
    For translation judges that have a natural scale (BLEU 0-100, COMET
    -1 to 1) we map onto [0, 1] before populating this field but also
    store the raw value in ``evidence`` for transparency.
    """

    name: str
    passed: bool
    score: float
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "score": self.score,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass
class PanelVerdict:
    """Aggregated outcome across all 11 judges."""

    overall_pass: bool
    verdict: str  # PASS | FAIL | BORDERLINE
    overall_score: int  # 0-100, integer for cheap JSON / on-chain encoding
    translation_scores: dict[str, Any]  # {"bleu": ..., "comet": ..., "mqm": {...}}
    style_alignment_passes: dict[str, bool]  # {"d1": True, ..., "d8": True}
    judge_results: list[JudgeResult]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall_pass": self.overall_pass,
            "verdict": self.verdict,
            "overall_score": self.overall_score,
            "translation_scores": self.translation_scores,
            "style_alignment_passes": self.style_alignment_passes,
            "judge_results": [jr.as_dict() for jr in self.judge_results],
            "notes": list(self.notes),
        }


@dataclass
class PanelQuestion:
    """A question payload tailored to the quality panel.

    This is intentionally a superset of
    :class:`polyglot_alpha.polymarket.types.Question` since the panel
    needs the source-language news, resolution metadata, and category to
    judge style alignment. The dataclass accepts dict-style construction
    via :meth:`from_mapping` so callers can hand it the sample JSONs in
    ``outputs/`` directly.
    """

    title: str
    description: str = ""
    resolution_criteria: str = ""
    resolution_source: str = ""
    cutoff_ts: str = ""
    category: str = ""
    source_news: str = ""
    source_language: str = "zh"
    target_language: str = "en"
    reference_translation: Optional[str] = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PanelQuestion":
        return cls(
            title=str(payload.get("title", "")),
            description=str(payload.get("description", "") or ""),
            resolution_criteria=str(payload.get("resolution_criteria", "") or ""),
            resolution_source=str(payload.get("resolution_source", "") or ""),
            cutoff_ts=str(payload.get("cutoff_ts", "") or ""),
            category=str(payload.get("category", "") or ""),
            source_news=str(payload.get("source_news", "") or ""),
            source_language=str(payload.get("source_language", "zh") or "zh"),
            target_language=str(payload.get("target_language", "en") or "en"),
            reference_translation=payload.get("reference_translation"),
        )
