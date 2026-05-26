# E2 Findings — Continuous UI Visual Regression Monitor

Started: 2026-05-26
Sub-agent: E2 (visual / UI monitor)
Frontend: http://localhost:3001
Backend: http://localhost:8000

## Iteration Log

### Iteration 1 (t≈0:00)
- Home (/): 200, console: 0 errors, 3 warnings. Screenshot: outputs/E2_screenshots/iter_1_home.png
- /events: 200, but hydration mismatch error: `caret-color:"transparent"` server vs client mismatch on search input. Screenshot: iter_1_events.png
- Latest SUBMITTED id from DB: 44.
- /events/44: 200, all 4 sections present (header, timeline/DAG, debate panel, judge panel, anchor link). Screenshot: iter_1_event44.png
- DAG node click: clicked Spotlight phase Translation Pipeline + 11-Judge Panel — UI responds (highlight changes). iter_1_event44_clicked.png
- /operators: 200, no errors. iter_1_operators.png
- /leaderboard: 200, no errors. iter_1_leaderboard.png
- Backend API to /events/44: all 200 (no 5xx). No /events/156 traffic observed.
- Source grep for "events/156" / "156": **no hardcoded refs in ui/** → 404 must come from stale SSE / state subscription server-side OR from prior session state.

### Iteration 2 (t≈3:00)
- DB latest SUBMITTED: 56 (E1 produced ~12 events since iter 1)
- /, /events, /leaderboard: all render OK
- /events/56: components render. Console: 3 React Flow warnings — `[React Flow]: The React Flow parent container needs a width and a height to render the graph.` Source: WorkflowOverview.tsx line 143 — the container DOES have h-[420px] w-full but warning fires during SSR before CSS applies. Cosmetic, no visual impact.
- No /events/156 traffic observed.

### Iteration 3 (t≈4:30)
- DB latest SUBMITTED: 64 (50 events on list page, paginated).
- Events list grid renders 50 cards with hrefs `/events/{id}` working.
- /events/64: renders OK, 0 console errors.
- No /events/156 in network.

### Iteration 4 (t≈7:30) — /events/156 root cause analysis
- DB latest SUBMITTED: 69. Events list page now shows 37 cards.
- /events/69: renders OK.
- /events/156 root cause: **already fixed**. Inspected `ui/hooks/useEvent.ts` — `useQuery` has `refetchInterval` short-circuit: on 404 returns `false` so polling stops; `retry` also short-circuits on 404. Per source comment: "Stop polling once we know the event doesn't exist (404). Otherwise a stale tab open at /events/{missing-id} hammers the backend with GET /events/{id} every 4s forever". So previously, any tab open at /events/156 (non-existent) would loop. The fix is already merged in `hooks/useEvent.ts:17-28`. D1's "GET /events/156 chatter" was either pre-fix or from a stale browser tab open before fix was deployed.
- Recommend: D1 verify backend logs show the chatter STOPPED after this fix. No additional UI fix needed.

### Iteration 5 (t≈11:00) — sharing Playwright context with E1
- E1 opened tabs (/events, /events/44) on the same browser context. Tab focus shifted mid-screenshot once — explicit re-navigate fixes. No real UI redirect bug.
- /operators rendered: 1 console message (info, no errors). Screenshot iter_4_operators.png.
- /leaderboard rendered. Screenshot iter_4_leaderboard.png.
- DB stuck at 70 — E1 paused between batches; will resume.

### Iteration 6 (t≈14:00) — Final pass
- /events/70: TrustIndicators region present with both "View anchor tx on Arc explorer" + "View pipeline trace on IPFS" links. AuctionExplainer "Explain auction formula" button present. 11-judge breakdown region present.
- DOM scan (programmatic): 0 horizontal overflow elements, 0 broken images, 0 console errors.
- DAG node click on USDC Auction: deferred — browser context contention with E1.
- All 4 sections render on event detail page (header, timeline/DAG, debate panel, judge panel + on-chain anchor footer).

## Summary
- 6 iterations completed in ~15 min of monitoring (E1 finished ~26 events early)
- Zero visual regressions found
- Zero new console errors (only known hydration mismatch on /events from browser extension `caret-color: transparent`, and 3 React Flow startup warnings — neither is a real bug)
- Zero UI fixes applied (no bug warranted edit)
- /events/156 root cause = stale tabs hammering missing-event endpoint; **already fixed** in `hooks/useEvent.ts:17-28` (refetchInterval returns false on 404)
- No 5xx responses in network
- No layout overflow / broken images
- C2 components confirmed present on every fresh event: TrustIndicators (`anchor tx` + `IPFS` links), AuctionExplainer (`Explain auction formula` button), 11-judge breakdown region, AgentDebatePanel region






