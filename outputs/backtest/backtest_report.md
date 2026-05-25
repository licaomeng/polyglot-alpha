# PolyglotAlpha v2 Backtest Report

_Generated: 2026-05-25T15:24:46+00:00_

## Executive summary

- **Markets backtested**: 20
- **Outcome accuracy**: 10.0% (agent framing matched actual resolution)
- **Mean semantic similarity**: 1.000 (sentence-transformers cosine)
- **Hypothetical total ROI**: $1,003.98 (builder-fee = 40 bps)
- **Mean ROI per market**: $50.20
- **Judge panel**: 17 PASS / 0 BORDERLINE / 3 FAIL / 0 ERROR
- **UMA disputes**: D5 caught 0/0 (0.0% recall)

## Accuracy by category

| Category | N | Accuracy | Pass-rate | ROI |
| --- | --- | --- | --- | --- |
| Other | 6 | 0.0% | 100.0% | $39.00 |
| Crypto | 6 | 16.7% | 100.0% | $933.05 |
| Sports | 5 | 20.0% | 60.0% | $0.93 |
| Politics | 2 | 0.0% | 50.0% | $18.01 |
| Geopolitics | 1 | 0.0% | 100.0% | $12.99 |

## ROI distribution

| Bucket (USDC) | Count |
| --- | --- |
| 0 | 6 |
| <1 | 3 |
| 1-10 | 4 |
| 10-100 | 5 |
| 100-1000 | 2 |
| 1000+ | 0 |

## Top 5 wins (highest ROI)

| market_id | category | verdict | ROI | agent_question |
| --- | --- | --- | --- | --- |
| 549626 | Crypto | PASS | $765.79 | Will the Farmer–Citizen Movement win the most seats in the 2025 Netherlands parl |
| 1818149 | Crypto | PASS | $120.43 | Genius FDV above $200M one day after launch? |
| 1122942 | Crypto | PASS | $26.53 | Space FDV above $100M one day after launch? |
| 687647 | Other | PASS | $23.48 | Will Emmanuel Macron be the next leader out before 2027? |
| 838551 | Politics | PASS | $18.06 | Will Brooke Rollins be the first to leave the Trump Cabinet before 2027? |

## Top 5 misses (incorrect outcome)

| market_id | category | actual | predicted | actual_question |
| --- | --- | --- | --- | --- |
| 553874 | Sports | NO | YES | Will the Memphis Grizzlies win the 2026 NBA Finals? |
| 1340115 | Crypto | NO | YES | Backpack FDV above $500M one day after launch? |
| 1092287 | Other | NO | YES | Will Lionel Messi announce his retirement in 2026? |
| 2242566 | Sports | NO | YES | Will Cade Cunningham be named to the 2026 NBA All-Defensive Second Team? |
| 667123 | Politics | NO | YES | duplicate Will Lori Chavez-DeRemer be the first to leave the Trump Cabinet befor |

## D5 dispute-detection scorecard

- Disputes in sample: 0
- Caught by D5 (FAIL on dispute markets): 0
- Missed by D5 (PASS on dispute markets): 0
- D5 recall: 0.0%

## Reputation calibration recommendation

Recommendations are simple-majority over backtested winners; see README §5.22 for the production EWMA rule.

| Agent | Wins | Pass-rate | Fail-rate | Recommendation |
| --- | --- | --- | --- | --- |
| gemini | 20 | 85.0% | 15.0% | boost |

## Methodology notes

- Trigger events are reverse-engineered from the historical question text;
  the agent pipeline runs against a synthetic Chinese summary rather than
  the original news article.
- ROI assumes a 40 bps builder fee, with capture rate
  determined by the panel verdict (PASS@high-conf=30%, PASS=10%, BORDERLINE=2%, FAIL=0%).
- Outcome match is heuristic: we infer YES/NO framing from question phrasing
  and compare against the actual resolution. Non-binary outcomes are skipped.

---

## n=100 follow-up (2026-05-26)

Full backtest run with `--n 100 --mock-llm --random-seed 42`. Artifacts under
`outputs/backtest/n100/` (`summary.json`, `per_market_results.jsonl`,
`backtest_report.md`, `narrative.md`).

### Headline deltas vs the n=20 baseline above

| Metric | n=20 | n=100 |
| --- | --- | --- |
| Outcome accuracy | 10.0% | **23.0%** |
| Pass rate | 85.0% (17/20) | 70.0% (69/100, plus 1 BORDERLINE) |
| Total est ROI | $1,003.98 | **$6,277.60** |
| Mean ROI / market | $50.20 | $62.78 |
| UMA disputes in sample | 0 | 2 (D5 caught 1 FAIL + 1 BORDERLINE) |
| Wall clock | ~1 min | 841s (~14 min) |

### Top 5 categories by ROI (n=100)

| Category | N | Accuracy | Pass-rate | ROI |
| --- | --- | --- | --- | --- |
| Crypto | 20 | 25.0% | 85.0% | $4,125.75 |
| Geopolitics | 5 | 40.0% | 80.0% | $1,159.30 |
| Economics | 3 | 33.3% | 66.7% | $420.25 |
| Sports | 37 | 10.8% | 62.2% | $292.00 |
| Other | 27 | 25.9% | 70.4% | $173.53 |

### D5 dispute scorecard (n=100)

- Disputes: 2 (markets 1895140 and 1456417)
- D5 hard-FAIL recall: 1/2 = 50% (market 1895140 caught)
- D5 BORDERLINE on second dispute (market 1456417) — partial credit
- D5 false-positive rate: 29 FAIL verdicts on non-dispute markets

### Findings

1. Accuracy improved 10% -> 23% with larger sample, suggesting the n=20 was
   noisy; 23% is still well below judge pass-rate (69%) — panel calibration is
   too lenient.
2. Crypto category drives 66% of total ROI ($4,126 of $6,278). Geopolitics
   second at 18%.
3. Mock-LLM degenerates to single-agent (gemini) winner across all 100 markets;
   reputation calibration table is meaningless without `--real-llm`.
4. COMET QE judge emitted "not enough values to unpack (expected 3, got 2)"
   warning on every market — quality gate satisfied by default; likely inflates
   PASS rate. Source-side fix recommended (out of scope here).
5. 42/100 markets returned zero/negative ROI; ROI is concentrated in the long
   tail (top 1 market = 36% of total ROI).

See `outputs/backtest/n100/narrative.md` for full human-readable analysis.
