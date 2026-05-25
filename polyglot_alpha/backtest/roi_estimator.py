"""ROI estimator.

Hypothetical: had our system listed a market for a historical resolved
Polymarket question, how much would the winning translator have earned
from builder fees?

Model:

* Builder fee = ``BUILDER_FEE_BPS`` of fill notional (default 40 bps = 0.40%).
* Capture rate (what fraction of the historical market's USDC volume
  we would have intercepted) depends on the panel verdict and judge
  confidence — strong PASS captures more, FAIL captures nothing.
* Outcome-correctness multiplier: a wrongly-framed question would have
  drawn liquidity but the winning translator still earns the builder
  fee (the LP, not the translator, eats the inventory loss).

The estimator returns both the gross builder fee and the
"counterfactual ROI" (fee minus a fixed agent cost stub) so the report
can show net contribution per market.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


BUILDER_FEE_BPS: float = 40.0  # 40 bps = 0.40%
"""Builder fee rate applied to fill notional. Source: README §3 demo."""

# Capture rate by judge verdict bucket. ``high_confidence`` is PASS with
# overall_score >= ``HIGH_CONFIDENCE_THRESHOLD`` (panel returns 0-100).
CAPTURE_RATE_PASS_HIGH: float = 0.30
CAPTURE_RATE_PASS: float = 0.10
CAPTURE_RATE_BORDERLINE: float = 0.02
CAPTURE_RATE_FAIL: float = 0.0

HIGH_CONFIDENCE_THRESHOLD: int = 85
"""Panel score (0-100) above which we treat a PASS as high-confidence."""

# Stub cost per agent run: LLM API + pipeline overhead. Used so net ROI
# can be negative for losing markets.
AGENT_RUN_COST_USDC: float = 0.05


@dataclass(frozen=True)
class RoiEstimate:
    """Per-market ROI estimate."""

    capture_rate: float
    captured_volume_usdc: float
    builder_fee_usdc: float
    agent_cost_usdc: float
    net_roi_usdc: float

    def as_dict(self) -> dict:
        return asdict(self)


def _capture_rate_for(verdict: str, score: float) -> float:
    """Map (verdict, score) -> capture rate."""

    verdict_upper = (verdict or "").upper()
    if verdict_upper == "PASS":
        if score >= HIGH_CONFIDENCE_THRESHOLD:
            return CAPTURE_RATE_PASS_HIGH
        return CAPTURE_RATE_PASS
    if verdict_upper == "BORDERLINE":
        return CAPTURE_RATE_BORDERLINE
    return CAPTURE_RATE_FAIL


def estimate_roi(
    actual_volume_usdc: float,
    judge_verdict: str,
    judge_score: float,
    *,
    builder_fee_bps: float = BUILDER_FEE_BPS,
    agent_cost_usdc: float = AGENT_RUN_COST_USDC,
) -> RoiEstimate:
    """Estimate USDC earnings for a single backtested market.

    Args:
        actual_volume_usdc: Historical total volume on the real market.
        judge_verdict: ``PASS`` / ``BORDERLINE`` / ``FAIL``.
        judge_score: Panel score in [0, 100].
        builder_fee_bps: Override builder fee (default 40 bps).
        agent_cost_usdc: Override per-run cost stub.
    """

    volume = max(0.0, float(actual_volume_usdc or 0.0))
    capture_rate = _capture_rate_for(judge_verdict, judge_score)
    captured_volume = volume * capture_rate
    builder_fee = captured_volume * (builder_fee_bps / 10_000.0)
    net = builder_fee - agent_cost_usdc
    return RoiEstimate(
        capture_rate=capture_rate,
        captured_volume_usdc=captured_volume,
        builder_fee_usdc=builder_fee,
        agent_cost_usdc=agent_cost_usdc,
        net_roi_usdc=net,
    )
