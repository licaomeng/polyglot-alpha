# Type Safety Audit Report

Date: 2026-05-26
Working dir: `/Users/messili/codebase/polyglot-alpha`
Mode: READ-ONLY (no source files modified)

## Summary
- mypy strict errors: **127** (across 30 of 76 files)
- TypeScript current errors: **11** (strict mode already enabled in `ui/tsconfig.json`)
- TypeScript strict-mode errors: **11** (same — strict was already on)
- Python `Any` usages (grep `\bAny\b|: any`): **200**
- TS `any` usages (in app/components/hooks/lib): **2 files, 0 `: any` annotations** (both are `as any` casts)
- `# type: ignore` markers: **26** (23 of which mypy now reports as `unused-ignore`)
- `@ts-ignore` / `@ts-expect-error`: **0**

## mypy strict findings

### Top error categories (count)
| # | Category | Count | Notes |
|---|---|---|---|
| 1 | `[bool]` | 66 | Almost entirely SQLAlchemy `Select.where()` false positives — mypy sees Python `bool` instead of `ColumnElement[bool]` |
| 2 | `[attr-defined]` | 24 | Missing/dynamic attributes (web3, polymarket SDKs, mocks) |
| 3 | `[unused-ignore]` | 23 | Stale `# type: ignore` comments that no longer apply |
| 4 | `[arg-type]` | 23 | Mismatched argument types (SQLAlchemy + Pydantic boundary) |
| 5 | `[type-arg]` | 20 | Missing generic type parameters (`dict`, `list`, `Optional`) |
| 6 | `[no-any-return]` | 15 | Functions return `Any` from declared concrete return types |
| 7 | `[no-untyped-def]` | 11 | Missing function annotations |
| 8 | `[typeddict-item]` | 3 | TypedDict key mismatches |
| 9 | `[assignment]` | 3 | Incompatible target types |
| 10 | other | 3 | union-attr, str, no-untyped-call, no-redef, dict-item, call-overload |

### Top files (by error count)
| Errors | File |
|---|---|
| 13 | `polyglot_alpha/onchain.py` |
| 13 | `polyglot_alpha/corpus/lookup_db.py` |
| 12 | `polyglot_alpha/orchestrator.py` |
| 11 | `polyglot_alpha/api/routes/agents.py` |
| 8 | `polyglot_alpha/corpus/embed.py` |
| 7 | `polyglot_alpha/corpus/lookup.py` |
| 6 | `polyglot_alpha/polymarket/fill_listener.py` |
| 6 | `polyglot_alpha/judges/style_alignment/llm_batch.py` |
| 6 | `polyglot_alpha/corpus/db_ingestion.py` |
| 5 | `polyglot_alpha/corpus/resolved_scraper.py` |

### Files with highest `Any` density
| `Any` count | File |
|---|---|
| 31 | `polyglot_alpha/corpus/full_scraper.py` |
| 12 | `polyglot_alpha/orchestrator.py` |
| 12 | `polyglot_alpha/api/routes/events.py` |
| 10 | `polyglot_alpha/judges/style_alignment/d8_duplicate_detection.py` |
| 10 | `polyglot_alpha/ingestion/rss_aggregator.py` |
| 10 | `polyglot_alpha/corpus/db_ingestion.py` |

## TypeScript findings

### Without strict mode (N/A — already strict)
The project's `ui/tsconfig.json` already sets `"strict": true`. Running with strict false would only reduce errors further; the current 11-error count is the strict-mode baseline.

### With strict mode enabled
- Errors: **11**
- All failures are the **same root cause**: `Property 'toBeInTheDocument' does not exist on type 'JestMatchers<HTMLElement>'`
- Files (all under `__tests__/`):
  - `__tests__/Badge.test.tsx` (1)
  - `__tests__/EmptyState.test.tsx` (3)
  - `__tests__/EventStatusBadge.test.tsx` (3)
  - `__tests__/RealVsMockBadge.test.tsx` (4)
- **Fix**: add `import "@testing-library/jest-dom"` reference in `jest.setup.js` types, or add `/// <reference types="@testing-library/jest-dom" />` in a shared `.d.ts`, or install `@types/testing-library__jest-dom` if not yet present.

### `any` usage in TS (very low)
- `app/page.tsx:1` — `(featured as any).phases` cast on workflow data
- `lib/api.ts:1` — `(window as any).__POLYGLOT_API_BASE__` global lookup

No `: any` annotations and no `@ts-ignore` anywhere in `app/components/hooks/lib`. Excellent baseline.

## Recommendations

1. **Fix the 11 TS errors with a one-line types reference**
   Add `@testing-library/jest-dom` types to `jest.setup.js`/global types — eliminates 100% of current TS errors. After this fix, TS is fully clean under strict.

2. **Replace SQLAlchemy `bool` false positives**
   ~66 `[bool]` errors and many `[arg-type]` errors come from SQLAlchemy `Select.where(...)`. Use `sqlalchemy.sql.elements.ColumnElement[bool]` annotations or wrap predicates so mypy infers them correctly. Alternatively, plug in `sqlalchemy-stubs`/`sqlalchemy2-stubs` plugin in `pyproject.toml` `[tool.mypy]`. This single change would remove ~50 % of the 127 mypy errors.

3. **Sweep `# type: ignore` markers**
   23 of 26 are now `unused-ignore`. Removing them is a mechanical, zero-risk cleanup that drops the error count by ~18 %.

### mypy strict per-file enablement order
- **Easiest (1 error)** — flip strict on per-file first via `[[tool.mypy.overrides]]`:
  `api/routes/builder_fees.py`, `backtest/roi_estimator.py`, `backtest/runner.py`,
  `corpus/reference_loader.py`, `corpus/resolved_analysis.py`, `corpus/style_guide.py`,
  `ingestion/models.py`, `judges/panel.py`,
  `judges/style_alignment/d8_duplicate_detection.py`,
  `judges/translation/comet_judge.py`, `pubsub.py`
- **Medium (2-6)** — `llm.py`, `translators.py`, `ingestion/cross_reference.py`,
  `ingestion/rss_aggregator.py`, `polymarket/mock_client.py`,
  `polymarket/client.py`, `polymarket/fill_listener.py`,
  `corpus/embed.py`, `corpus/db_ingestion.py`, `corpus/lookup.py`,
  `api/routes/events.py`, `backtest/outcome_matcher.py`,
  `judges/style_alignment/llm_batch.py`
- **Hardest (11-13)** — `onchain.py`, `corpus/lookup_db.py`, `orchestrator.py`,
  `api/routes/agents.py`

## Overall type-safety grade

**B+**

- **Frontend: A** — TypeScript strict already on, almost no `any`, no `@ts-ignore`, only 11 errors all from one missing test-types reference (trivial fix).
- **Backend: C+** — Strict mypy yields 127 errors in 30 of 76 files (~40 %). Density of `Any` (200) and the orchestrator/onchain hotspots indicate genuine type debt, though a large fraction (~50 %) is SQLAlchemy stub friction rather than real safety holes.
- Weighted overall is **B+**: strong frontend hygiene; backend has actionable, mostly mechanical cleanup to reach a solid A.
