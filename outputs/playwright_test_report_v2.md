# Playwright E2E Test Report v2 (after fixes)

Generated: 2026-05-25 (run 2) · backend http://localhost:8000 · frontend http://localhost:3001

## Summary

- Pages tested: 8 / 9 (skipped only the v2_05 "leaderboard sorted" — could not click sortable header because the page never hydrated; details below)
- Screenshots captured: 8 (`v2_01_home`, `v2_02_events`, `v2_03_event_detail`, `v2_04_leaderboard`, `v2_06_agent_profile`, `v2_07_history`, `v2_08_about`, `v2_09_trigger_running`)
- Previously RED → now: **GREEN at the backend / API contract layer, RED at the rendered-UI layer due to a single, unrelated, server-side regression** (stale `.next/` dev cache → client JS never loads)
- Critical bugs from v1: **6 of 6 actually fixed in code** (B1, B2, B3, B4, B5, B6 all verified at API + SSE level); none re-verifiable via rendered UI because of the hydration regression
- New bug found: **1 critical** (frontend never hydrates because `next dev` is serving HTML referencing chunk paths that 404; affects every client-rendered route)

## Bug verification table

| Bug | v1 status | v2 backend / API | v2 rendered UI | Evidence |
|---|---|---|---|---|
| B1 list shape | Crashed 3 pages w/ `events.slice is not a function` | **FIXED** — `/events`, `/leaderboard`, `/builder_fees` all return bare `[...]` arrays | Could not visually verify (no hydration), but no `events.slice` error in console anywhere | `curl /events` → array of 17 |
| B2 event-detail fields | Empty page (no winner/verdict/score/market_id/phases) | **FIXED** — `/events/8` returns `winner_address=0xqwen_agent`, `verdict=PASS`, `overall_score=0.62`, `market_id=mock-26945ed2f7c5`, `phases[7]`, `bids[2]` | Not rendered (page shell only) | API JSON inspected |
| B3 agent fields | Crashed `Cannot read properties of undefined` on reputation | **FIXED** — `/agents/0xllama_agent` returns `address`, `reputation`, `totalRevenue`, `wins`, `losses`, `winRate`, plus `history[]` | Not rendered (page shell only) | API JSON inspected |
| B4 judges always FAIL | All events REJECTED | **FIXED** — events 1-5 still legacy FAIL but events **8, 10, 14, 15, 16, 17 = PASS** (mock pipeline now emits 200+ char resolution_criteria + valid title) | Not visually verifiable | `/events/{id}` verdict field |
| B5 missing endpoints | 404 on `/events/{id}/phases`, `/translations`, `/builder_fees` | **FIXED** — all three return HTTP 200 | n/a (server-side) | `curl -o /dev/null -w "%{http_code}"` = 200 / 200 / 200 |
| B6 SSE silent | Only `hello` event ever delivered | **FIXED** — full lifecycle of 10 event types observed in browser EventSource: `hello`, `event.created`, `auction.opened`, `bid.submitted`, `auction.settled`, `translation.completed`, `quality.verdict`, `onchain.committed`, `polymarket.submitted`, `builder_fee.accrued` | n/a | Confirmed in-page via `browser_evaluate` + curl `xxd` of stream |

## Per-page findings

### 1. `/` (home) — `v2_01_home.png`
- HTTP 200, page title "Polyglot Alpha v2", banner + nav + footer render (SSR'd shell)
- WorkflowOverview ReactFlow widget is present in the snapshot (an `application` ARIA role with Zoom-In / Zoom-Out / Fit-View controls) — markup landed
- "Featured events (0)" — empty because client-side `useEventList` never executes (hydration broken)
- "Trigger live demo" button present in DOM but clicking it would no-op (no event handler attached)
- **No `events.slice` TypeError anywhere** in console

### 2. `/events` — `v2_02_events.png`
- Shows header + search box + 4 filter buttons (`all/running/completed/live`)
- 0 event cards rendered AND 0 skeleton AND 0 "No matching events" empty state — proves `isLoading` is permanently `undefined` because React Query never starts
- Backend confirms 17 events available
- No crash, no `events.slice` error

### 3. `/events/8` — `v2_03_event_detail.png`
- Page renders SSR shell only (no h1, no winner badge, no timeline)
- Backend `/events/8` JSON is fully populated: id, headline, status SUBMITTED, verdict PASS, winner_address 0xqwen_agent, overall_score 0.62, market_id mock-26945ed2f7c5, `final_question.resolution_criteria` is a 200+ char string, `phases[]` has 7 entries with `tx_hash`/`details`, `bids[]` has 2 entries

### 4. `/leaderboard` — `v2_04_leaderboard.png`
- SSR shell only; no table rendered
- Backend `/leaderboard` returns 5 agents sorted by rank: `0xqwen_agent` (rank 1, rep 0.617, 8 wins, $8 revenue), `0xllama_agent`, `0xgemini_agent`, `0xdeepseek_agent`, plus one `0xbbbb…bbbb` test agent
- Could not click the "Reputation" header to test sort interactivity (no hydration → no click handlers) — screenshot v2_05 intentionally omitted

### 5. `/agents/0xllama_agent` — `v2_06_agent_profile.png`
- SSR shell only
- Backend: `reputation=0.0`, `totalRevenue=0.0`, `wins=0`, `losses=11`, `winRate=0.0`, `history[3]` with timestamps — **all fields B3 promised**

### 6. `/history` — `v2_07_history.png`
- SSR shell only; filter table not rendered

### 7. `/about` — `v2_08_about.png` — **GREEN**
- This page **DOES render content** (h1 "Mechanism design", 1464 chars of body text) because it's static markup with no client-only hooks → SSR HTML is self-sufficient
- Confirms the hydration regression is the sole frontend blocker

### 8. Trigger live demo (curl path) — `v2_09_trigger_running.png`
- UI button click no-ops, but curl `POST /trigger/event` works perfectly
- Triggered events 15, 16, 17 all completed full 7-phase lifecycle in ~1s each, all PASS verdicts, all got `market_id`, all phases emitted SSE
- Screenshot shows the home page (no in-UI feedback because nothing hydrated)

### 9. SSE listener — verified in-browser
- Full 10-event-type sequence received within ~3s of trigger. **B6 conclusively fixed.**

## NEW bug found — CRITICAL (single root cause for all UI-side failures)

**Bug F1 — Next.js dev server serves HTML referencing chunk paths that 404; client JS never executes; no page hydrates.**

- Console (every page): `404 on /_next/static/chunks/main-app.js`, `/_next/static/chunks/app-pages-internals.js`, `/_next/static/chunks/app/page.js`, `/_next/static/chunks/app/layout.js`, `/_next/static/css/app/layout.css`, `/_next/static/css/app/page.css`
- Frontend log: `webpack.cache.PackFileCacheStrategy ... Resolving './vendor-chunks/next-themes' ... doesn't lead to expected result ... resolving dependencies are ignored`
- Filesystem: `.next/static/chunks/app/page-e8c653abe0ac664c.js` (hashed prod-style filename) exists; `.next/static/chunks/app/page.js` (dev-style unhashed) does NOT
- Diagnosis: `next dev` was started on top of a previous `next build` artifact tree. The dev server is reading stale HTML manifests that point to nonexistent dev paths. All `"use client"` components (i.e. every interactive route except `/about`) silently fail to hydrate → `useEventList` / `useEventDetail` / `useAgent` / `useLeaderboard` never fire.
- Fix (out of scope for this run per "do not restart servers"): `rm -rf /Users/messili/codebase/polyglot-alpha/ui/.next && (kill the existing next dev pid 70149) && npm run dev`. Single fix unblocks all 6 UI bugs.
- Impact: **all six fixes B1-B6 are real and live in backend code, but a demo viewer would still see blank pages.**

## Demo readiness verdict

**YELLOW** — backend and pipeline are fully demo-ready (every promised fix works at the API + SSE layer, lifecycle completes end-to-end with PASS verdicts and market submission in ~1s). However, the **frontend will not render any data** in its current process state. A 30-second `rm -rf .next && next dev` recycle moves the demo to GREEN.

## Recommendations (prioritized)

1. **Critical:** restart the frontend with a clean `.next/` directory before recording the Loom (`cd /Users/messili/codebase/polyglot-alpha/ui && rm -rf .next && pkill -f "next-server" && env NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 npm run dev`). Without this nothing on the screen will update.
2. After restart, re-run this exact playwright plan; the 8 screenshots should then show real content.
3. Consider adding a status indicator on the home page that polls `/sse/events` or `/healthz` so a stale-server condition is visible to viewers (not just to devs reading the console).
4. Add a `redirect to /events` empty-state CTA on `/` when `featured_events` is empty (right now the heading "All events (0)" + empty grid is ambiguous).
5. The events list filter buttons (`running`/`completed`/`live`) won't match the backend's UPPERCASE `SUBMITTED` status verbatim — add either backend lowercasing or a frontend mapper (`SUBMITTED` → `completed`) before the demo. Otherwise the filter pills look broken even after hydration is fixed.
6. Once hydration is restored, sanity-check the "Trigger live demo" button actually calls `/trigger/event` (it's wired in source but unverifiable here).

## File outputs

- `outputs/playwright_test_report_v2.md` (this file)
- `outputs/screenshots/v2_01_home.png` through `v2_09_trigger_running.png` (8 PNGs; v2_05 omitted — see §4)
