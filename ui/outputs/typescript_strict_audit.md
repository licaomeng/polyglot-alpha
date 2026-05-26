# TypeScript Strict-Mode Audit — PolyglotAlpha v2 Frontend

Scope: `/Users/messili/codebase/polyglot-alpha/ui/`
Date: 2026-05-26
tsconfig: `"strict": true`, `target: ES2020`, `moduleResolution: bundler`, `types: ["jest", "node", "@testing-library/jest-dom"]`

## TL;DR

The frontend already compiles cleanly under `tsc --noEmit --strict`. **Baseline = 0 errors**, post-cleanup = 0 errors. The 11 previously-reported jest-dom errors were resolved prior to this audit by adding `@testing-library/jest-dom` to the `types` array in `tsconfig.json`.

Because there were no errors to fix, the audit pivoted to type-debt hygiene. The largest smell — four `as unknown as { … }` casts in `EventTimeline.tsx` — was removed by widening the canonical `EventDetail` type with the snake_case fallback fields the backend already emits. All 36 jest tests still pass.

## Iteration 1 — Baseline

| Metric                          | Value |
| ------------------------------- | ----- |
| `tsc --noEmit --strict` exit    | 0     |
| Error count                     | 0     |
| `: any` / `as any` (prod)       | 0     |
| `: any` / `as any` (tests)      | 3 (intentional, EventStatusBadge fallback test) |
| `@ts-ignore` / `@ts-expect-error` | 0   |
| Non-null assertions (`x!.y`)    | 1 (justified, see below) |
| `as unknown as { … }` casts     | 4     |
| jest                             | 36/36 in 8/8 suites |
| TS/TSX file count (prod)        | 57    |
| TS/TSX file count (tests/mocks) | 8     |

Re-run with `tsconfig.tsbuildinfo` deleted to ensure no stale incremental cache — still 0 errors.

### Categorization

No `error TS…` codes were produced, so the requested top-error-code / top-error-file tables are empty by construction. Categorization N/A for this codebase.

### Type-debt inventory

- `hooks/useEventStream.ts:225` — `data.phase!.name` inside an `else if (data.phase)` branch. The assertion is correct; the compiler narrows lost across the closure boundary. Acceptable.
- `components/event/EventTimeline.tsx:272-277` — four casts of the shape `(event as unknown as { foo?: string }).foo` to read snake_case fallbacks the backend emits but the `EventDetail` interface omitted. Fixable.
- `__tests__/EventStatusBadge.test.tsx` — three `as any` casts that feed invalid status strings to verify badge fallback rendering. Intentional; kept.

## Iteration 2 — Cleanup

Only one targeted edit was made, since there were no errors to chase:

1. `lib/api.ts` — added four optional snake_case fallback fields (`builder_code`, `market_id`, `market_url`, `is_simulated`) on `EventDetail` with a comment documenting why the backend exposes them.
2. `components/event/EventTimeline.tsx` — replaced
   ```ts
   const topLevelBuilderCode = (event as unknown as { builder_code?: string })
     .builder_code;
   // …three more…
   ```
   with direct accesses (`event.builder_code`, etc.). 6 lines removed, 4 added.

### Post-cleanup metrics

| Metric                          | Before | After |
| ------------------------------- | ------ | ----- |
| `tsc --noEmit --strict` errors  | 0      | 0     |
| `as unknown as { … }` casts     | 4      | 0     |
| Non-null assertions             | 1      | 1     |
| `: any` (prod)                  | 0      | 0     |
| jest tests passing              | 36/36  | 36/36 |

## Outstanding type debt

Very low. The only remaining items worth tracking:

- One justified non-null assertion in `useEventStream.ts` — could become `if (data.phase) { … }` re-narrowing if a strictness-pedant reviewer asks.
- Three test-side `as any` casts in `EventStatusBadge.test.tsx` — intentional fallback-coverage; replacing them with a sentinel type would slightly improve safety but is not worth the churn.
- `lib/api.ts` carries `Record<string, unknown>` for a few payload blobs (e.g. `polymarket.payload`) — appropriate for opaque backend JSON, not debt.

No `// @ts-ignore`, no `// @ts-expect-error`, no `eslint-disable`, no `Record<string, any>`. The codebase is in good strict-mode shape.

## Constraints honored

- No backend edits.
- No commit / push.
- No new `as any`.
- No new `@ts-ignore`.
- All 36 jest tests still passing.
- Wall clock < 30 min.
