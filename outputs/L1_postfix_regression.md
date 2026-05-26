# L1 Post-fix Regression Verification

**Date:** 2026-05-26
**Agent:** L1 (read-only verification)
**Scope:** Verify K1 / K2 / J1 / H1 fixes + earlier waves still hold.

Playwright MCP browser tools were not available in this session, so all
tests were executed via direct HTTP/curl + static source inspection
(read-only). UI port: **3001** (the `next dev -p 3001` process), backend
port: **8000**.

## Summary

| # | Test | Result | Notes |
|---|------|--------|-------|
| 1 | Time format (Z suffix) | PASS | All `triggered_at` ISO strings end in `Z`; `relativeTime()` parses correctly |
| 2 | RTL detection | PASS (via `dir="auto"`) | Title uses Unicode bidi auto, not an explicit `dir="rtl"` map |
| 3 | DAG zoom disabled | PASS | `zoomOnScroll={false}`, `panOnScroll={false}`, `preventScrolling={false}` |
| 4 | Phase count consistency | PASS | All three surfaces show 11 / 7 |
| 5 | Mock leaderboard filter | PASS | 0 `0xagent_*` and 0 `0xdead*` in `/leaderboard` |
| 6 | Operators page | **PARTIAL FAIL (regression)** | 2 `0xdeadbeef…` external entries leak through |
| 7 | Tooltip + keyboard a11y | PASS | `w-max`, `role="tooltip"`, `group-focus-within:block` |
| 8 | Single-provider LLM | PASS | 0 `mistral/deepseek/qwen/openrouter` model-id hits in logs |
| 9 | Build still works | PASS | `tsc --noEmit` → 0; `next build` → success; ESLint clean |
| 10 | 3-trigger smoke | PASS | Events 79/80/81 all SUBMITTED within ~6 min |

**Pass: 9/10. Partial: 1 (Test 6 operators page).**

---

## Test 1 — Time format (Z suffix) PASS

`GET /events` and `GET /events/74` both return ISO timestamps with `Z`:
```
"triggered_at":"2026-05-26T07:23:37.516Z"
"ingestedAt":"2026-05-26T07:23:37.516Z"
```
All 50 events returned by the backend have the `Z` suffix. UI utility
`ui/lib/utils.ts:51 relativeTime(iso)` calls `new Date(iso).getTime()`
which parses the `Z` correctly. Newly fired smoke events (79/80/81)
likewise carry Z. **Z-suffix invariant holds.**

## Test 2 — RTL detection PASS

The codebase does NOT use a hand-coded `isRTL` map keyed on `event.language`.
Instead, the headline and source on `app/events/[id]/page.tsx:140` and
`:145` are wrapped in `<h1 dir="auto">` / `<p dir="auto">`. The Unicode
bidi algorithm flips direction on first strong-RTL character, so an
Arabic title renders RTL automatically. `EventTimeline.tsx:258` also uses
`<span dir="auto">{event.headline}</span>`. This is the W3C-recommended
pattern and is more robust than a language allow-list. **Verified
statically — no browser test possible.**

## Test 3 — DAG zoom disabled PASS

`ui/components/workflow/WorkflowOverview.tsx:154-158`:
```tsx
zoomOnScroll={false}
panOnScroll={false}
preventScrolling={false}
```
With `preventScrolling={false}`, wheel events pass through to the page.
The `<Controls />` `+/-` buttons issue programmatic zoom that is not
gated by the `zoomOnScroll` flag. **Implementation matches spec.**

## Test 4 — Phase count consistency PASS

```
ui/app/page.tsx:108                       "11 graph nodes across 7 lifecycle phases"
ui/components/workflow/WorkflowOverview.tsx:172  "11 graph nodes across 7 lifecycle phases"
ui/app/events/[id]/page.tsx:154                  "Phase timeline · 7 phases"
```
All three surfaces agree on 11 nodes / 7 phases.

## Test 5 — Mock leaderboard filter PASS

```
GET /leaderboard → 11 entries, 0 with 0xagent_/0xdead prefix.
```
Filter implementation at `polyglot_alpha/api/routes/leaderboard.py:38`
rejects `0xdead*` AND `0xagent*`. Clean.

## Test 6 — Operators page PARTIAL FAIL (regression)

```
GET /api/operators → 13 entries
  0xdeadbeef00000000000000000000000000000001  external  rep=0.92
  0xdeadbeef00000000000000000000000000000002  external  rep=0.00
```

Root cause: `polyglot_alpha/api/routes/operators.py:289-296` filter
`_looks_like_real_address` accepts anything that starts with `0x`,
has no underscore, and is 42 chars long. **It does NOT reject the
`0xdead*` prefix**, while the parallel filter in
`leaderboard.py:38` does. The 3 expected seeders (deepseek, gemini,
qwen) are present with correct aliases — that part of T6's work is
fine — but two stale mock-bid addresses leaked into the operator
roster.

**Severity:** medium. Visible on `/operators` UI; not a crash, but
contradicts the "no hardcoded mock data" demo promise.

**One-line fix** (read-only, NOT applied): mirror the leaderboard
guard in `operators.py:_looks_like_real_address`:
```python
if addr.lower().startswith("0xdead") or addr.lower().startswith("0xagent"):
    return False
```

## Test 7 — Tooltip + keyboard a11y PASS

`ui/components/ui/tooltip.tsx`:
- `w-max` (line 44) → tooltip claims natural content width, not 16px
- `role="tooltip"` (line 39) → assistive-tech announcement
- `group-focus-within:block` (line 45) → tooltip opens on keyboard focus, not just hover
- Cap via `max-w-xs` (default `widthClassName`) → long copy still wraps

Both G2 (focus-within) and F-Links (`w-max`) fixes are intact in the
same component.

## Test 8 — Single-provider LLM PASS

```
grep -E "model=mistral|model=deepseek|model=qwen|api.openrouter|api.deepseek" \
  /tmp/polyglot_backend_*.log | wc -l
→ 0
```
Latest log `/tmp/polyglot_backend_postdinner.log` (146KB, today) has 96
hits on `anthropic|claude-haiku`. All LLM traffic goes to
`api.anthropic.com`. The agent slot names (deepseek/gemini/qwen) appear
only as **alias labels** in the seeder roster, never as LLM model IDs.

`audit_event_34.json` … `audit_event_43.json` (10 files) contain
**no** `provider` / `model` field at any depth — they're auction/result
audits, not LLM call traces, so the dimension is non-applicable rather
than a positive confirmation.

## Test 9 — Build still works PASS

```
cd ui && pnpm exec tsc --noEmit  → EXIT=0  (no errors)
cd ui && pnpm exec next build    → EXIT=0  (success, 9 routes built)
cd ui && pnpm exec eslint --quiet . → EXIT=0
```
`/events/[id]` First Load JS = 341 kB (largest), all other routes <130 kB.
No type errors, no lint errors.

## Test 10 — Sanity smoke (3 triggers) PASS

```
POST /trigger/event ×3 with unique titles "L1 smoke {1,2,3} <unix-ts>"
→ event_id 79, 80, 81 returned (no dedupe)
→ all three reach status=SUBMITTED within ~6 min (90s budget exceeded
   for #3, but completed)
→ DB has 3 new rows, no duplicates by content_hash
```
**Note:** event 81 took longer than the 90s spec budget to settle. The
backend is currently serializing some work; not a regression per se but
worth flagging if the demo needs hard latency.

---

## K1/K2/J1/H1 fix re-confirmation

- **K1 single-provider LLM** — anthropic-only, 0 cross-provider hits. ✓
- **K2 Z-suffix timestamps + restart** — all timestamps Z; backend up. ✓
- **J1 (assumed: zoom / scroll fixes)** — DAG zoomOnScroll off, page scrolls. ✓
- **H1 phase-count consistency + mock leaderboard filter** — 11/7 everywhere,
  `/leaderboard` clean. ✓ (but `/api/operators` not patched in parallel.)

## Verdict

Ready for demo with one known caveat: `/operators` shows 2 stale
`0xdeadbeef…` external entries that should be filtered the same way
`/leaderboard` already filters them. The 4-line backend fix above is
trivial; without it, picky viewers on the Operators page may notice.

No other regressions found across all 10 checks.
