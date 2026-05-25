# PolyglotAlpha v2 Backtest Report

_Generated: 2026-05-25T18:56:37+00:00_

## Executive summary

- **Markets backtested**: 100
- **Outcome accuracy**: 23.0% (agent framing matched actual resolution)
- **Mean semantic similarity**: 1.000 (sentence-transformers cosine)
- **Hypothetical total ROI**: $6,277.60 (builder-fee = 40 bps)
- **Mean ROI per market**: $62.78
- **Judge panel**: 69 PASS / 1 BORDERLINE / 30 FAIL / 0 ERROR
- **UMA disputes**: D5 caught 0/2 (0.0% recall)

## Accuracy by category

| Category | N | Accuracy | Pass-rate | ROI |
| --- | --- | --- | --- | --- |
| Sports | 37 | 10.8% | 62.2% | $291.99 |
| Other | 27 | 25.9% | 70.4% | $173.53 |
| Crypto | 20 | 25.0% | 85.0% | $4,125.75 |
| Politics | 6 | 33.3% | 33.3% | $65.79 |
| Geopolitics | 5 | 40.0% | 80.0% | $1,159.30 |
| Economics | 3 | 33.3% | 66.7% | $420.25 |
| Pop-Culture | 2 | 100.0% | 100.0% | $40.99 |

## ROI distribution

| Bucket (USDC) | Count |
| --- | --- |
| 0 | 42 |
| <1 | 18 |
| 1-10 | 12 |
| 10-100 | 18 |
| 100-1000 | 9 |
| 1000+ | 1 |

## Top 5 wins (highest ROI)

| market_id | category | verdict | ROI | agent_question |
| --- | --- | --- | --- | --- |
| 549626 | Crypto | PASS | $2,297.48 | Will the Farmer–Citizen Movement win the most seats in the 2025 Netherlands parl |
| 1422365 | Geopolitics | PASS | $789.46 | Will the US strike Somalia next? |
| 1021154 | Economics | PASS | $419.02 | Will JPMorgan Chase or any of its underwriting affiliates serve as the lead unde |
| 690699 | Crypto | PASS | $383.26 | Sentient FDV above $200M one day after launch? |
| 1818149 | Crypto | PASS | $361.39 | Genius FDV above $200M one day after launch? |

## Top 5 misses (incorrect outcome)

| market_id | category | actual | predicted | actual_question |
| --- | --- | --- | --- | --- |
| 553874 | Sports | NO | YES | Will the Memphis Grizzlies win the 2026 NBA Finals? |
| 1340115 | Crypto | NO | YES | Backpack FDV above $500M one day after launch? |
| 1092287 | Other | NO | YES | Will Lionel Messi announce his retirement in 2026? |
| 2242566 | Sports | NO | YES | Will Cade Cunningham be named to the 2026 NBA All-Defensive Second Team? |
| 667123 | Politics | NO | YES | duplicate Will Lori Chavez-DeRemer be the first to leave the Trump Cabinet befor |

## D5 dispute-detection scorecard

- Disputes in sample: 2
- Caught by D5 (FAIL on dispute markets): 0
- Missed by D5 (PASS on dispute markets): 2
- D5 recall: 0.0%

## Reputation calibration recommendation

Recommendations are simple-majority over backtested winners; see README §5.22 for the production EWMA rule.

| Agent | Wins | Pass-rate | Fail-rate | Recommendation |
| --- | --- | --- | --- | --- |
| gemini | 100 | 69.0% | 30.0% | hold |

## Methodology notes

- Trigger events are reverse-engineered from the historical question text;
  the agent pipeline runs against a synthetic Chinese summary rather than
  the original news article.
- ROI assumes a 40 bps builder fee, with capture rate
  determined by the panel verdict (PASS@high-conf=30%, PASS=10%, BORDERLINE=2%, FAIL=0%).
- Outcome match is heuristic: we infer YES/NO framing from question phrasing
  and compare against the actual resolution. Non-binary outcomes are skipped.
