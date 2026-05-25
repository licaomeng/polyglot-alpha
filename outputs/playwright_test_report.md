# Playwright E2E Test Report

## Summary
- Pages tested: 9/9
- Screenshots: outputs/screenshots/*.png (9 files)
- Critical bugs: 5
- Medium bugs: 3
- Cosmetic: 2

**Headline finding:** Every single data-driven page on the frontend has a hard runtime `TypeError` or an empty render because the backend returns paginated `{items: [...]}` objects while the React components expect bare arrays / different field shapes. The app is effectively non-functional past the `<header>` and `/about` page.

## Per-page findings

### 1. Home `/`
- Render: BROKEN (full-page Next.js error overlay)
- Console errors: 13 (1 unique repeated)
- Network failures: none (200 OK), but data contract mismatch
- Bugs:
  - **CRIT-1**: `TypeError: events.slice is not a function` at `app/page.tsx:121:20` and `app/page.tsx:331:90`. Backend `GET /events` returns `{items, limit, offset}`, frontend treats `events` as `Array`. Need to either unwrap `.items` in the data fetch hook or change backend contract.
  - **COSMETIC-1**: favicon 404 (`/favicon.ico` not found)
  - **MEDIUM-1**: React warning "Cannot update a component while rendering a different component" in HomePage — setState during render

### 2. Events list `/events`
- Render: BROKEN (full-page error overlay)
- Console errors: 14
- Bugs:
  - **CRIT-1 (same root cause)**: `TypeError: data.filter is not a function` at `app/events/page.tsx:21:8`. Same `{items}` vs array issue.
- Could not exercise filtering / search / click into card because page crashes.

### 3. Event detail `/events/{id}`
- Render: Page LOADS but is functionally EMPTY (no error, but no data).
- Bugs:
  - **CRIT-2 (B1 from demo validation, CONFIRMED)**: `GET /events/{id}` drops `winner_address`, `verdict`, `overall_score`, `market_id`, `phases`, `bids` from the response. Only returns 7 top-level fields: `id, content_hash, sources, language, title, triggered_at, status`. Compare to `POST /trigger/event` which DOES return `verdict` + `overall_score`. The event-detail GET serializer is missing fields.
  - Net effect: `<h1>` is empty, phase timeline `<ol>` is empty, status shows just "REJECTED ingested —".
  - ReactFlow workflow DAG renders correctly (it's static UI not dependent on event data).
  - SSE channel says "sse connected" — works.
- Interactions tested: navigated directly; no phases to expand.

### 4. Leaderboard `/leaderboard`
- Render: OK but shows EMPTY STATE "No agents yet" even though backend has 5 ranked agents.
- Bugs:
  - **CRIT-3**: Backend `GET /leaderboard` returns `{sort_by, items: [...]}`. Frontend Leaderboard page presumably expects an array. The empty-state heading shows because `items` is treated as undefined/empty.
  - No revenue chart visible (no data to chart anyway).

### 5. Agent profile `/agents/0xllama_agent`
- Render: BROKEN (error overlay)
- Bugs:
  - **CRIT-4**: `TypeError: Cannot read properties of undefined (reading 'toFixed')` at `components/reputation/AgentProfile.tsx:18`. Code reads `agent.reputation.toFixed(2)`. Backend `GET /agents/{addr}` returns `{agent_address, total_bids, total_wins, avg_quality, cumulative_fees, last_updated}` — **no `reputation` field, no `totalRevenue` field, no `address` field** (uses `agent_address`). Field-name mismatch between API and UI types.

### 6. History `/history`
- Render: BROKEN (error overlay)
- Bugs:
  - **CRIT-1 (same root cause)**: `TypeError: data.filter is not a function` at `app/history/page.tsx:23:8`. Same `{items}` vs array.

### 7. About `/about`
- Render: OK (only page that fully renders).
- Console errors: 0
- Network failures: 0
- Content seen: hero text, footer, mechanism note. Visually clean.

### 8. Trigger lifecycle test
- Posted event via `POST /trigger/event`: YES (event_id=6, status=REJECTED, verdict=FAIL, overall_score=0.53)
- First trigger was deduped (status REJECTED, deduped=true) — content_hash collision detection works.
- Event appeared in `/events` list: YES (visible in API; UI crashes before render)
- Reached PASS: **NO** — all 7 events in DB are status=REJECTED. Bug B2 (final cleanup agent didn't fix) is **confirmed still present**.
- Reached Polymarket submit: NO (gated on PASS verdict)
- Builder fees accrued: NO — `cumulative_fees=0.0` for every agent; `total_wins=0` for every agent.
- New event appeared at top: confirmed via API (id=7 BoJ event auto-created in addition).
- Screenshot evidence: `outputs/screenshots/09_new_event.png` (shows empty event-detail for the new event 6).

### 9. Console / network sweep
- Home: 13 errors (all `events.slice`)
- Events: 14 errors (all `data.filter`)
- Event detail: 1-3 console errors only (less catastrophic but data missing)
- Agent: 16 errors (toFixed cascade)
- History: 14 errors (data.filter)
- About: 0 errors
- Leaderboard: 0 errors (gracefully shows empty state)
- No CORS failures observed; all HTTP responses 200 OK from backend. The bug is purely **API contract mismatch**, not a network/auth/CORS issue.

### Additional backend endpoint surface discovered
| Endpoint | Status |
|---|---|
| `GET /events` | 200, returns `{items: [...]}` |
| `GET /events/6` | 200, returns top-level fields only (NO phases/winner/verdict) |
| `GET /events/6/phases` | **404** (UI expects this to exist for timeline) |
| `GET /events/6/bids` | 200, returns `{event_id, items}` |
| `GET /events/6/translations` | **404** |
| `GET /agents/0xllama_agent` | 200, missing `reputation` / `totalRevenue` |
| `GET /leaderboard` | 200, returns `{sort_by, items: [...]}` |
| `GET /builder_fees` | **404** |

## Recommendations (prioritized)

1. **CRIT-1 (blocker)**: Standardize list endpoints. Either (a) backend returns bare arrays for `/events` and `/leaderboard`, OR (b) every `useQuery` hook in `lib/api/*.ts` unwraps `.items`. Pick one and apply consistently across all pages. This single fix unblocks `/` , `/events`, `/history`, `/leaderboard`.

2. **CRIT-2 (B1, blocker for demo)**: Fix `GET /events/{id}` response model to include `winner_address`, `verdict`, `overall_score`, `market_id`, and a `phases: [...]` array. Without this, the event-detail page (the core demo surface) is a blank shell. Either embed phases in the detail response OR implement `GET /events/{id}/phases` so the frontend can fetch separately.

3. **CRIT-4 (blocker for `/agents/*` page)**: Fix `/agents/{addr}` response shape OR fix the frontend type. UI reads `agent.reputation`, `agent.totalRevenue`, `agent.address`; API returns `agent_address`, `cumulative_fees`, no reputation. Add `reputation`, alias `address := agent_address`, alias `totalRevenue := cumulative_fees` (or normalize in the React Query select-fn).

4. **CRIT-B2 (escalation needed)**: All 7 events in the database are status=REJECTED. The "final cleanup agent" claim that PASS pathway is fixed is **not borne out by reality** — confirm whether `auction_window_seconds: 1.0` is too short to complete the pipeline, or whether the verdict logic threshold is set too high. Backend logs need review.

5. **CRIT-3**: Implement missing `/builder_fees` endpoint OR remove the UI calls to it.

6. **MEDIUM-1**: Fix React setState-during-render warning in `app/page.tsx` (likely a `useEffect`-with-missing-deps issue).

7. **MEDIUM-2**: Implement `/events/{id}/phases` and `/events/{id}/translations` (both currently 404).

8. **MEDIUM-3 (B4)**: SSE `event.finalized` event missing — UI shows "sse connected" but never receives a finalized signal; could not verify because no events ever reach PASS.

9. **COSMETIC-1**: Add a `/favicon.ico` (404 on every page).

10. **COSMETIC-2**: Auto-spawned event id=7 (BoJ) appeared while testing — there's a background event-trigger running. Either expected or noisy; document or disable for demos.

## Demo readiness verdict: **RED**

5 of 9 pages are unusable due to JavaScript runtime errors. The single most important page (event detail) renders chrome but no data. No event has ever reached PASS, which means the Polymarket-submission and builder-fees storylines cannot be demonstrated end-to-end. **Do not record the Loom video until at least CRIT-1, CRIT-2, and CRIT-B2 are fixed.**
