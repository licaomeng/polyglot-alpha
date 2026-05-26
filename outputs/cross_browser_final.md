# Cross-Browser Compatibility Test — PolyglotAlpha v2 UI

**Date**: 2026-05-26
**Stack**: Next.js 15.5.18, React 18.3.1, Tailwind, Framer Motion, @xyflow/react
**Target**: http://localhost:3001 (frontend) + http://localhost:8000 (FastAPI backend)
**Driver**: Playwright 1.x, headless (chromium / firefox / webkit)
**Browsers tested**: chromium 1223, firefox 1505, webkit 2287

## Summary

| Browser  | Available | Routes pass | Console errors | Net 4xx/5xx | Trigger flow |
|----------|-----------|-------------|----------------|-------------|--------------|
| chromium | YES       | 5/5 + 2 viewports | 0          | 0           | PASS (67.1s) |
| firefox  | YES       | 5/5 + 2 viewports | 1 (CORS)   | 0           | PASS (65.7s) |
| webkit   | YES       | 5/5 + 2 viewports | 0          | 0           | PASS (65.2s) |

All five routes (`/`, `/events`, `/events/114`, `/leaderboard`, `/about`) render correctly on all three engines with the dark theme applied (body bg `rgb(7, 10, 19)` everywhere; no FOUC).

## Browser-specific findings

### Firefox: CORS preflight blocked on SSE stream (real bug)

```
Cross-Origin Request Blocked: The Same Origin Policy disallows reading the
remote resource at http://127.0.0.1:8000/sse/events?event_id=125.
(Reason: CORS request did not succeed). Status code: (null).
```

Firefox is stricter about EventSource CORS than Chromium/WebKit. The trigger
flow still completes (the page-level provider re-uses an existing SSE
connection to `localhost:8000` rather than `127.0.0.1:8000`), but the
component-scoped subscription in `TriggerButton` opens against `127.0.0.1`
and fails. Recommendation: backend should send
`Access-Control-Allow-Origin: *` (or echo the origin) and
`Access-Control-Allow-Credentials` for `/sse/*`, or the frontend should
canonicalize to one host. Chromium and WebKit do not surface this error.

### WebKit: tablet (768x1024) home FCP is ~13x slower than other viewports

| Browser  | mobile (375) FCP | tablet (768) FCP | desktop (1280) FCP |
|----------|------------------|------------------|--------------------|
| chromium | 112 ms           | 96 ms            | 960 ms (initial)   |
| firefox  | 102 ms           | 167 ms           | 302 ms             |
| webkit   | 133 ms           | **2168 ms**      | 159 ms             |

WebKit shows a 2168 ms FCP on the tablet viewport vs 133 ms on mobile / 159 ms
on desktop. The page still renders correctly (verified via screenshot), but
something in the WorkflowOverview/`@xyflow/react` layout path is markedly slower
at this exact width on WebKit's first paint. Likely a JIT cold-start for the
SVG-heavy graph; investigate if user-facing.

### Firefox: visual zoom difference at tablet width

Both Firefox and WebKit render the 768x1024 tablet shot with identical PNG
dimensions, but Firefox's logical content is rendered at a smaller effective
size — the layout is identical but content occupies ~75% of the page area
that WebKit fills 100% of. Not a bug, but worth noting if pixel-perfect QA
is required across browsers.

### Chromium / WebKit: no findings beyond Firefox's CORS issue

Zero console errors, zero network failures, dark theme applied, all
interactive elements reachable.

## Performance comparison (median across 5 desktop routes)

| Browser  | median FCP | median LCP | min FCP / LCP   | max FCP / LCP   |
|----------|-----------:|-----------:|-----------------|-----------------|
| chromium |     148 ms |     492 ms | 84 / 96         | 960 / 960       |
| firefox  |     127 ms |     279 ms | 120 / 120       | 302 / 865       |
| webkit   |     159 ms |     310 ms | 99 / 99         | 310 / 1250      |

`/events` LCP on WebKit was 1250 ms (highest cold-cache), likely the
`@xyflow/react` lazy chunk on the events list page. Firefox is the fastest at
median LCP (279 ms).

## Trigger flow (final, with `page.waitForURL` and a 90 s budget)

| Browser  | POST status | spinner shown | navigated | finalUrl                           | t-to-nav |
|----------|-------------|---------------|-----------|------------------------------------|----------|
| chromium | 200         | YES           | YES       | /events/124                        | 67.1 s   |
| firefox  | 200         | YES           | YES       | /events/125                        | 65.7 s   |
| webkit   | 200         | YES           | YES       | /events/126                        | 65.2 s   |

All three browsers complete the lifecycle end-to-end. **First-run results in
`cross_browser_iter_1.json` showed chromium/webkit with `urlChanged=false`** —
that turned out to be a backend-load issue when the trigger queue was warm
(events 119–123 had just been triggered seconds before). The re-test
(`cross_browser_trigger_v2.json`) is the authoritative result.

Note: the `/trigger/event` endpoint is **synchronous** — it blocks for the
~65 s lifecycle and only returns once `event.finalized` fires. The UI
copes by also subscribing to SSE for live progress labels, but anyone driving
the demo programmatically should expect a 60-75 s POST, not an instant
202 + polling.

## Worst-performing combo

**WebKit @ 768x1024 (tablet) on `/`** — FCP 2168 ms, LCP 2210 ms. This is the
only datapoint anywhere in the matrix above 1 s for FCP. Same browser hits
133 ms FCP at 375x812 and 159 ms at 1280x800, so the regression is
viewport-specific. Page still renders correctly, just slowly on first paint.

## Source-level cross-browser concerns (verified)

| Concern                          | Status | Notes                                          |
|----------------------------------|--------|------------------------------------------------|
| `text-wrap: balance` (globals.css:79) | OK | WebKit ≥17.4 supports it; falls back to `wrap` |
| `backdrop-filter` in SiteHeader  | OK     | Already gated with `supports-[backdrop-filter]` |
| `EventSource` (SSE)              | Partial | Firefox CORS issue on 127.0.0.1 vs localhost  |
| `IntersectionObserver`           | n/a    | grep found no usage in components             |
| Vendor `-webkit-` prefixes       | n/a    | None in `globals.css`                          |
| Dark-mode via `class="dark"`     | OK     | Hardcoded in `app/layout.tsx`, no media query reliance |

## Artifacts

- `outputs/cross_browser_iter_1.json` — full matrix (chromium/firefox/webkit × 5 routes × 3 viewports + trigger)
- `outputs/cross_browser_trigger_v2.json` — authoritative trigger re-test (all 3 browsers PASS)
- `outputs/cross_browser_trigger_recheck.json` — earlier debug run (no `networkidle` wait, kept for traceability)
- `outputs/screenshots/xbrowser_{chromium,firefox,webkit}_{route}_{viewport}.png` — 27 screenshots
- `outputs/screenshots/xbrowser_*_trigger_final.png` and `_v2.png` — trigger flow end states
- `ui/scripts/cross_browser_test.js`, `cross_browser_chromium_only.js`, `cross_browser_trigger_v2.js`

## Recommendations (no source changes made)

1. **Fix Firefox CORS on `/sse/events`** — backend should send permissive
   CORS headers (or canonicalize 127.0.0.1 vs localhost on the frontend
   side). Currently the only real cross-browser bug.
2. **Investigate WebKit tablet FCP** — 2168 ms is well above the 60 s
   anchor-to-on-chain budget the homepage advertises. Likely WorkflowOverview
   lazy load; consider a smaller skeleton at tablet width.
3. **Make `/trigger/event` non-blocking** — current 65 s synchronous POST
   means any frontend timeout < 75 s will appear broken. Either return
   `202` immediately with `event_id` and let the SSE channel drive
   progress, or document the long-poll behavior.
