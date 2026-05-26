"""W14-C/W14-CONTRACT-PREP: Simulate ReputationRegistry EMA score under demo conditions.

Mirrors the Solidity logic in ``contracts/src/ReputationRegistry.sol`` so we
can probe the formula without spinning up an EVM. All values are 1e18-scaled
to match on-chain fixed-point.

Two contract versions are simulated:

  * ``v1`` — the **deployed** ReputationRegistry. Has the unit-scale bug
    described in W9-B / W14-C: ``_fillSignal`` divides USDC-6-decimal units by
    ``FEE_SCALE=100`` then treats the result as a 1e18 fixed-point number, so
    ``fillSignal`` is permanently clamped at ``FILL_MIN=0.5`` for any realistic
    fee. Initial score on first touch is 1.0 (``ONE``).

  * ``v2`` — the **proposed** (not-yet-deployed) ReputationRegistry. Applies
    two fixes:
      - β: rescale fees with ``(cumFees * 1e12) / FEE_SCALE`` so 6-decimal USDC
        → 1e18 fixed-point before the ln() input.
      - α: initial score on first touch is 0.5 (``HALF``) instead of 1.0 so
        the first ``_recompute`` does not strictly decrease the score.

Run::

    python scripts/simulate_ema.py             # default: side-by-side v1 vs v2
    python scripts/simulate_ema.py --version v1
    python scripts/simulate_ema.py --version v2
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass


ONE = 10 ** 18
HALF = 5 * 10 ** 17                 # 0.5 — v2 initial score
DECAY = 85 * 10 ** 16              # 0.85
SIGNAL_W = 15 * 10 ** 16            # 0.15
FILL_MIN = 5 * 10 ** 17             # 0.5
FILL_MAX = 2 * 10 ** 18             # 2.0
FEE_SCALE = 100
SAT_X = 6_389_056_098_930_650_407   # e^2 - 1 in 1e18 units
LN2 = 693_147_180_559_945_309
USDC_DECIMALS = 6
USDC_TO_1E18 = 10 ** 12              # 1e18 / 1e6 — v2 β fix


def mul_div(a: int, b: int, d: int) -> int:
    return (a * b) // d


def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def fill_signal(cumulative_fees_units: int, *, version: str = "v1") -> int:
    """Port of ``_fillSignal``.

    ``cumulative_fees_units`` is in 6-decimal USDC base units (the exact value
    ``BuilderFeeRouter.updateOnFee`` passes to the registry).

    * ``v1`` reproduces the deployed bug: ``x = units / FEE_SCALE`` and then
      treats ``x`` as 1e18 fixed-point.
    * ``v2`` applies the β fix: ``x = (units * 1e12) / FEE_SCALE`` so the input
      is correctly rescaled to 1e18 fixed-point first.
    """
    if cumulative_fees_units == 0:
        return FILL_MIN

    if version == "v1":
        x = cumulative_fees_units // FEE_SCALE
    elif version == "v2":
        x = mul_div(cumulative_fees_units, USDC_TO_1E18, FEE_SCALE)
    else:
        raise ValueError(f"unknown version: {version!r}")

    if x >= SAT_X:
        return FILL_MAX
    if x > ONE:
        num = x - ONE
        den = SAT_X - ONE
        t = mul_div(num, ONE, den)
        interp = LN2 + mul_div(2 * ONE - LN2, t, ONE)
        return clamp(interp, FILL_MIN, FILL_MAX)
    x2 = mul_div(x, x, ONE)
    x3 = mul_div(x2, x, ONE)
    x4 = mul_div(x3, x, ONE)
    pos = x + x3 // 3
    neg = x2 // 2 + x4 // 4
    ln = pos - neg if pos > neg else 0
    return clamp(ln, FILL_MIN, FILL_MAX)


@dataclass
class Rep:
    total_bids: int = 0
    total_wins: int = 0
    total_quality_passes: int = 0
    cumulative_fees_units: int = 0
    score: int = 0          # set lazily on first touch via _lazy_init
    initialized: bool = False


def recompute(r: Rep, *, version: str = "v1") -> int:
    win_rate = ONE if r.total_bids == 0 else mul_div(r.total_wins, ONE, r.total_bids)
    quality_rate = ONE if r.total_wins == 0 else mul_div(r.total_quality_passes, ONE, r.total_wins)
    fs = fill_signal(r.cumulative_fees_units, version=version)
    wq = mul_div(win_rate, quality_rate, ONE)
    signal = mul_div(wq, fs, ONE)
    decayed = mul_div(r.score, DECAY, ONE)
    weighted = mul_div(signal, SIGNAL_W, ONE)
    return decayed + weighted


def _lazy_init(r: Rep, *, version: str) -> None:
    if not r.initialized:
        # α-fix: v1 inits to 1.0 (ONE); v2 inits to 0.5 (HALF).
        r.score = ONE if version == "v1" else HALF
        r.initialized = True


def update_on_auction(r: Rep, won: bool, *, version: str = "v1") -> None:
    _lazy_init(r, version=version)
    r.total_bids += 1
    if won:
        r.total_wins += 1
    r.score = recompute(r, version=version)


def update_on_quality(r: Rep, passed: bool, *, version: str = "v1") -> None:
    _lazy_init(r, version=version)
    if passed:
        r.total_quality_passes += 1
    r.score = recompute(r, version=version)


def update_on_fee(r: Rep, fee_usdc: float, *, version: str = "v1") -> None:
    _lazy_init(r, version=version)
    units = int(round(fee_usdc * 10 ** USDC_DECIMALS))
    r.cumulative_fees_units += units
    r.score = recompute(r, version=version)


def score_f(score_1e18: int) -> float:
    return score_1e18 / ONE


# -----------------------------------------------------------------------------
# Scenarios
# -----------------------------------------------------------------------------


def scenario(
    label: str,
    wins: int,
    fee_each: float,
    *,
    quality_passes_each: bool = True,
    version: str = "v1",
) -> dict:
    r = Rep()
    for _ in range(wins):
        update_on_auction(r, won=True, version=version)
        update_on_quality(r, passed=quality_passes_each, version=version)
        update_on_fee(r, fee_each, version=version)
    return {
        "label": label,
        "wins": wins,
        "fee_each_usdc": fee_each,
        "cum_fees_usdc": wins * fee_each,
        "win_rate": r.total_wins / max(r.total_bids, 1),
        "quality_rate": r.total_quality_passes / max(r.total_wins, 1) if r.total_wins else 1.0,
        "fill_signal": score_f(fill_signal(r.cumulative_fees_units, version=version)),
        "final_score": score_f(r.score),
    }


def reproduce_w9b(*, version: str = "v1") -> dict:
    """Reproduce W9-B observed sequence: clean win, quality pass, $0.9 fee."""
    r = Rep()
    update_on_auction(r, won=True, version=version)
    s1 = score_f(r.score)
    update_on_quality(r, passed=True, version=version)
    s2 = score_f(r.score)
    update_on_fee(r, 0.9, version=version)
    s3 = score_f(r.score)
    return {"after_auction": s1, "after_quality": s2, "after_fee": s3}


# -----------------------------------------------------------------------------
# Output helpers
# -----------------------------------------------------------------------------


SCENARIOS = [
    ("fresh agent, 1 win, $0.9 fee",          1,   0.9),
    ("fresh agent, 10 wins, $0.9 each",       10,  0.9),
    ("fresh agent, 100 wins, $0.9 each",      100, 0.9),
    ("fresh agent, 1 win, $1000 fee",         1,   1000.0),
    ("fresh agent, 1 win, $10000 fee",        1,   10000.0),
    ("fresh agent, 50 wins, $5 each",         50,  5.0),
]


def _print_version(version: str) -> None:
    print(f"\n=== version: {version} ===")
    w9b = reproduce_w9b(version=version)
    print(f"  W9-B sequence (win + quality-pass + $0.9 fee):")
    for k, v in w9b.items():
        print(f"    {k:>18}: {v:.6f}")

    print(f"  scenarios (auction-won + quality-pass + fee each event):")
    print(f"    {'scenario':<42} {'cumFees':>10} {'fillSig':>9} {'score':>8}")
    for label, wins, fee in SCENARIOS:
        s = scenario(label, wins, fee, version=version)
        print(
            f"    {s['label']:<42} ${s['cum_fees_usdc']:>8.2f} "
            f"{s['fill_signal']:>9.4f} {s['final_score']:>8.4f}"
        )


def _print_side_by_side() -> None:
    """Compact v1 vs v2 comparison table — the W14-C-mandated artifact."""
    print()
    print("=" * 80)
    print("v1 (deployed, buggy) vs v2 (proposed fix) — side-by-side")
    print("=" * 80)

    rows = []
    rows.append(("W9-B: 1 win + quality + $0.9 fee",
                 reproduce_w9b(version="v1")["after_fee"],
                 reproduce_w9b(version="v2")["after_fee"]))
    for label, wins, fee in SCENARIOS:
        sv1 = scenario(label, wins, fee, version="v1")
        sv2 = scenario(label, wins, fee, version="v2")
        rows.append((label, sv1["final_score"], sv2["final_score"]))

    print(f"{'Scenario':<42} | {'v1 score':>8} | {'v2 score':>8}")
    print("-" * 42 + "-+-" + "-" * 9 + "-+-" + "-" * 9)
    for label, v1, v2 in rows:
        print(f"{label:<42} | {v1:>8.4f} | {v2:>8.4f}")
    print()
    print("Notes:")
    print("  * v1 fillSignal collapses to FILL_MIN=0.5 for any realistic fee")
    print("    because USDC 6-decimal units pass through `units/100` and are then")
    print("    misinterpreted as a 1e18 fixed-point number (off by 1e12).")
    print("  * v1 first touch seeds score=1.0 (max), but the per-event signal is")
    print("    bounded around `winRate*qualityRate*0.5 = 0.5` mid-range, so the")
    print("    first update strictly *subtracts* (1.0 -> 0.7529 even on a clean win).")
    print("  * v2 β-fix rescales fees to 1e18 before the ln() input, so fillSignal")
    print("    spans the [0.5, 2.0] band naturally.")
    print("  * v2 α-fix seeds first touch at 0.5 (HALF) so the first event nets")
    print("    UP for a clean winner instead of dropping from a maxed-out prior.")


# -----------------------------------------------------------------------------
# Diagnostic: why v1 fillSignal collapses
# -----------------------------------------------------------------------------


def _print_collapse_diagnostic() -> None:
    print("\n[diagnostic] Why v1 fillSignal collapses to 0.5 (FILL_MIN) for demo fees:")
    print("    contract does: x = cumFeesUnits / FEE_SCALE(=100)")
    print("    then ln(1+x) where it ASSUMES x is 1e18-fixed-point")
    print("    but cumFeesUnits is in USDC 6-decimal units (usdc_to_units)")
    for fee in [0.9, 9.0, 90.0, 1000.0, 1_000_000.0]:
        units = int(fee * 10 ** USDC_DECIMALS)
        x_after_scale_v1 = units // FEE_SCALE
        x_after_scale_v2 = mul_div(units, USDC_TO_1E18, FEE_SCALE)
        x_as_float_v1 = x_after_scale_v1 / ONE
        x_as_float_v2 = x_after_scale_v2 / ONE
        print(
            f"      fee=${fee:>10.2f}  units={units:>14}  "
            f"v1 x_post_scale={x_after_scale_v1:>14}  (~{x_as_float_v1:.2e})  "
            f"v2 x_post_scale={x_after_scale_v2:>14}  (~{x_as_float_v2:.4f})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate ReputationRegistry EMA score (v1=deployed, v2=proposed fix)"
    )
    parser.add_argument(
        "--version",
        choices=("v1", "v2", "both"),
        default="both",
        help="Which contract version to simulate (default: both — prints side-by-side table).",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("W14-CONTRACT-PREP: EMA reputation simulation")
    print("  mirrors contracts/src/ReputationRegistry.sol (v1 + v2)")
    print("=" * 80)

    if args.version in ("v1", "both"):
        _print_version("v1")
    if args.version in ("v2", "both"):
        _print_version("v2")
    if args.version == "both":
        _print_side_by_side()
    _print_collapse_diagnostic()


if __name__ == "__main__":
    main()
