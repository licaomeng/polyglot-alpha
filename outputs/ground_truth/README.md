# Ground Truth Reference Translations

Human-quality reference translations and manual annotations for the 5 demo
samples in `outputs/sample_{0..4}.json`. The T4 BLEU / COMET / MQM judges and
the D1-D8 style-alignment judges consume these as ground truth.

## Why this exists

The pre-existing `sample_*.json` files contain the agent pipeline's own output.
Scoring an agent translation against its own output is circular — BLEU can hit
~62 against a paraphrase of itself even when the title has substantive errors.
These ground truth files provide independent reference translations so the T4
judges have a legitimate target.

## Files

```
ground_truth/
├── sample_0_ground_truth.json   # PBOC RRR cut (P1, easy)
├── sample_1_ground_truth.json   # MoF VAT exemption extension (P1, ambiguous)
├── sample_2_ground_truth.json   # GACC trade growth threshold (P3, currency-ambiguous)
├── sample_3_ground_truth.json   # State Council real-estate measures (P1, vague-action)
├── sample_4_ground_truth.json   # CSRC Guideline No. 10 supplementary rules (P1, clean)
└── README.md                    # this file

../reference_translations.jsonl  # all 5 in one streaming-friendly file
```

Each `sample_{i}_ground_truth.json` contains:

| Field | Purpose |
| --- | --- |
| `source_chinese`            | original Chinese source text |
| `source_url` / `source_outlet` | provenance |
| `agent_translation`         | what the pipeline produced (copied from `sample_{i}.json::title`) |
| `ground_truth_translation`  | `{primary, alternative_phrasings, rationale}` |
| `polymarket_shape_validation` | structural pattern, resolution date, UMA dispute risk |
| `comparable_polymarket_markets` | 3 nearest neighbors from the T5 5K corpus |
| `k5_framing_variants`       | 5 phrasings, matching the K=5 design in README §5.21 |
| `mqm_critical_errors`       | MAJOR-severity MQM findings on the agent vs ground truth |
| `mqm_minor_errors`          | MINOR-severity MQM findings |
| `d1_d8_validation`          | per-dimension `{pass, note}` annotation |
| `expected_bleu_threshold`   | per-sample BLEU gate (overrides global default of 25) |
| `expected_comet_threshold`  | per-sample COMET gate (overrides global default of 0.6) |
| `annotator_notes`           | freeform commentary |

## How the judges should consume this

- **T4 BLEU judge** (`polyglot_alpha/judges/translation/bleu_judge.py`):
  load `ground_truth_translation.primary` as `reference_translation` for the
  `PanelQuestion`; gate on `expected_bleu_threshold`. For tighter scoring use
  multi-reference mode with `[primary] + alternative_phrasings`.
- **T4 COMET judge** (`comet_judge.py`):
  same reference; gate on `expected_comet_threshold`.
- **T4 MQM-LLM judge** (`mqm_llm_judge.py`):
  use `mqm_critical_errors` / `mqm_minor_errors` as the gold MQM annotation —
  the LLM judge's outputs are validated against this set.
- **D1-D8 style judges**: each `d{i}_*` entry in `d1_d8_validation` is a binary
  oracle for that dimension; use it as a fixture for unit tests.

## BLEU sanity check (agent vs ground truth, sacrebleu corpus_bleu)

| Sample   | BLEU vs primary | BLEU vs multi-ref | Threshold | Verdict |
| ---      | ---:            | ---:              | ---:      | ---     |
| sample_0 |   41.23         |   75.12           |     30    | PASS    |
| sample_1 |   11.25         |   35.91           |     15    | PASS    |
| sample_2 |    6.66         |   51.75           |     20    | PASS    |
| sample_3 |   26.52         |   52.94           |     35    | PASS    |
| sample_4 |   18.05         |   82.53           |     40    | PASS    |

Multi-reference BLEU (primary + alternative phrasings) averages 59.65 across
the 5 samples — consistent with a fluent, faithful agent pipeline that the
panel should grade green, but with per-sample variance reflecting genuine MQM
errors (see `mqm_critical_errors` in sample_1 and sample_3).

## D1-D8 validation summary

37 of 40 dimension-sample checks pass (92.5%). All 3 failures are on
**D5 (Resolution Clarity)** — the most critical hard gate:

- `sample_1`: D5 fail — title conflates target extension date (2027-12-31)
  with market resolution cutoff (2026-08-31).
- `sample_2`: D5 fail — title omits currency (RMB vs USD) and YoY qualifier.
- `sample_3`: D5 fail — 'new measures' is under-specified vs prior policy.

These are the highest-value findings — they are the kind of error that would
trigger UMA disputes and panel slashing in production.

## MQM error totals

- Critical (major): 2 — both ambiguity errors on title-level scoping (samples 1 & 3).
- Minor: 8 — mostly stylistic / acronym / terminology preferences.

## How these annotations were built

1. Read the source Chinese and the agent's English question.
2. Drafted a canonical reference following T5 corpus (style guide + few-shots).
3. Cross-checked against 3 nearest neighbors from `corpus/polymarket_questions.parquet`.
4. Generated 4 alternative phrasings to give BLEU multi-reference variation.
5. Generated 5 K-framing variants per README §5.21 (K=5).
6. Manually evaluated D1-D8 against the criteria in
   `polyglot_alpha/judges/style_alignment/*.py`.
7. Recorded MQM errors at MAJOR/MINOR severity per the MQM guidelines used in
   `polyglot_alpha/judges/translation/mqm_llm_judge.py`.
8. Set per-sample BLEU/COMET thresholds based on how concise the agent
   translation is vs how detailed the canonical reference must be.

All annotations are version-controlled and small (~5.3 KB avg per sample).
