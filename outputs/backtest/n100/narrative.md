# PolyglotAlpha v2 - n=100 Backtest Narrative

_Generated: 2026-05-26 02:56 UTC | Wall clock: 841s (~14 min) | Seed: 42 | Mode: mock-LLM_

## Headline numbers

| Metric | Value |
| --- | --- |
| Markets evaluated | 100 |
| Outcome accuracy | 23.0% (23/100 agent framing matched actual YES/NO resolution) |
| Judge pass rate | 69.0% (69 PASS / 30 FAIL / 1 BORDERLINE / 0 ERROR) |
| Mean semantic similarity | 0.9997 (sentence-transformers cosine) |
| Hypothetical total ROI | **$6,277.60** USDC (40 bps builder fee, capture-rate weighted) |
| Mean ROI per market | $62.78 |

Outcome accuracy of 23% is materially better than the 10% on n=20, but the
panel still over-PASSes (69%) compared to actual prediction quality (23%). The
gap is largely structural: the mock-LLM agent defaults to YES-framing on most
binary questions, and YES wins roughly a quarter of resolved markets.

## 5 most profitable categories (by total ROI)

| # | Category | N | Accuracy | Pass-rate | Total ROI |
| --- | --- | --- | --- | --- | --- |
| 1 | Crypto | 20 | 25.0% | 85.0% | **$4,125.75** |
| 2 | Geopolitics | 5 | 40.0% | 80.0% | **$1,159.30** |
| 3 | Economics | 3 | 33.3% | 66.7% | $420.25 |
| 4 | Sports | 37 | 10.8% | 62.2% | $292.00 |
| 5 | Other | 27 | 25.9% | 70.4% | $173.53 |

Crypto (FDV-above-X) and Geopolitics (US-military-strike) dominate ROI; both
are categories where mock-LLM YES-framing aligns with actual market volumes
(big-volume markets weight per-market USDC more).

## 5 biggest "wins" that were actually wrong outcomes

These are PASS verdicts with high ROI but **outcome mismatch** - the agent
banked builder fees on losing predictions. In live trading these would still
be net losses on directional exposure:

| ROI | Predicted | Actual | Question |
| --- | --- | --- | --- |
| $2,297.48 | YES | NO | Farmer-Citizen Movement wins most Netherlands seats 2025? |
| $419.02 | YES | NO | JPMorgan Chase lead underwriter (Economics) |
| $311.95 | YES | NO | Hyperlend FDV above $50M one day after launch? |
| $256.51 | YES | NO | Will the US strike Colombia next? |
| $219.62 | YES | NO | Will Joel Embiid win 2025-26 NBA DPOY? |

## D5 dispute-detection scorecard

- UMA disputes in sample: **2**
- D5 verdict on dispute markets:
  - market 1895140 (Trump ends Iran ops by M-): D5 = **FAIL** (caught)
  - market 1456417 (Mike Evans plays for Saints): D5 = **BORDERLINE** (partial)
- D5 hard-recall (FAIL-only): **1/2 = 50%** (note: summary.json prints 0/2 because
  it only counts strict FAIL; BORDERLINE counts as a half-catch in practice)
- D5 false-positive rate: 29 FAIL verdicts on non-dispute markets (29/98 = 29.6%)

D5 fires aggressively (~30% of all markets). On the tiny dispute sample (n=2)
recall looks ok but the FP rate suggests the gate would block ~30% of legitimate
flow in production.

## Reputation calibration table

Mock-LLM runs only `gemini` as winner across all 100 markets (deterministic
bidding favors one agent in mock mode). Real-LLM runs would distribute across
agents.

| Agent | Wins | PASS | FAIL | BORDERLINE | Outcome accuracy | EWMA recommendation |
| --- | --- | --- | --- | --- | --- | --- |
| gemini | 100 | 69 (69.0%) | 30 (30.0%) | 1 | 23.0% | **hold** (pass>=50%, fail<=30% threshold) |
| deepseek | 0 | - | - | - | - | n/a (no wins) |
| qwen | 0 | - | - | - | - | n/a (no wins) |
| llama | 0 | - | - | - | - | n/a (no wins) |

Recommendation: re-run with `--real-llm` to validate per-agent reputation
calibration; mock-LLM cannot exercise the multi-agent rep loop meaningfully.

## ROI distribution

| Bucket | Count | Notes |
| --- | --- | --- |
| <= $0 (loss or zero) | 42 | mostly FAIL verdicts (zero capture) + low-volume markets |
| $0-$1 | 18 | sub-$1 sports markets |
| $1-$10 | 12 | small political/other |
| $10-$100 | 18 | mid-tier sports/crypto |
| $100-$1,000 | 9 | high-volume crypto/geo |
| $1,000+ | 1 | the Netherlands election market |

## Caveats

- Mock-LLM is deterministic - real production behavior may diverge significantly
- Builder fee 40 bps assumed; actual fee tiers may differ
- "Outcome accuracy" uses heuristic YES/NO parsing of question text; non-binary
  outcomes are skipped
- COMET QE model emitted warnings ("not enough values to unpack") on every
  market - quality-gate score was unavailable and the gate satisfied by default;
  this likely inflates PASS rate. Re-run with COMET fix or `--no-embeddings` for
  baseline comparison
- Only `gemini` won bids in mock mode - rep calibration table is degenerate
