"""Backtest framework for PolyglotAlpha v2.

Replays the 4-agent auction + 11-judge panel against historical
Polymarket resolved markets so we can answer: "would our system have
predicted these outcomes correctly, and what builder-fee revenue would
it have captured?"

Public surface:

* :func:`run_backtest` (in ``runner``) — full pipeline entry point.
* :func:`compare_questions` (in ``outcome_matcher``) — semantic +
  framing comparison between agent-generated and actual market
  questions.
* :func:`estimate_roi` (in ``roi_estimator``) — hypothetical USDC
  earnings from builder fees.
* :func:`generate_report` (in ``reporter``) — human-readable markdown
  report builder.
"""

from __future__ import annotations

from .outcome_matcher import OutcomeComparison, compare_questions
from .roi_estimator import RoiEstimate, estimate_roi
from .runner import BacktestResult, run_backtest

__all__ = [
    "BacktestResult",
    "OutcomeComparison",
    "RoiEstimate",
    "compare_questions",
    "estimate_roi",
    "run_backtest",
]
