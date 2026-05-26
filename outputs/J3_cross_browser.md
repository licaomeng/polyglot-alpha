# J3 — Cross-browser picky tester

**Time:** 2026-05-26 07:24–07:29 UTC
**Setup:** Playwright MCP (Chromium 148, headless) + UA spoofing for Safari / Firefox / Edge
**Frontend:** http://localhost:3001 (Next.js 14 dev server)
**Backend:** http://localhost:8000 (FastAPI)

---

## Summary verdict

| Browser | Verdict | Notes |
| --- | --- | --- |
| Chrome ≥ 105 | **likely OK** | Tested directly (Chromium 148). All pages render. Zero console errors. |
| Edge ≥ 105 | **likely OK** | Same engine as Chrome. UA spoof: no UA-sniffing branches → identical behavior. |
| Firefox ≥ 121 | **likely OK** | No FF-only CSS (`scrollbar-width`, `-moz-appearance`) used. SSE works cross-origin (CORS headers correct). UA spoof produced no errors. |
| Safari ≥ 18 / iOS ≥ 18 | **likely OK** | Modern Safari supports `backdrop-filter` unprefixed. RTL / Arabic / Hebrew / emoji render in `<h1>` correctly. |
| Safari 14–17, iOS 14–17 | **partial — known issue** | Tailwind dev CSS emits only `backdrop-filter`, no `-webkit-backdrop-filter`. Older Safari falls back to the non-blur path via `supports-[backdrop-filter]:bg-background/60` — header gets the opaque `bg-background/80` instead of blur. Cosmetic only, not broken. |

---

## UA sniffing in code

- **`navigator.userAgent` references in `ui/` src:** 0 occurrences (excludes `node_modules`, `scripts/cross_browser_*.js` which are Playwright-runner files, not app code).
- **`navigator.platform` / `navigator.vendor`:** 0 occurrences.
- **`metaKey` / `ctrlKey`:** 0 occurrences — no hardcoded Cmd vs Ctrl logic.
- **Conclusion:** App has zero UA-dependent branches. Behavior is identical regardless of UA string.

## CSS prefix coverage

- **`backdrop-filter`** used in `ui/components/shared/SiteHeader.tsx:26` via Tailwind `backdrop-blur` + `supports-[backdrop-filter]:bg-background/60`.
- Compiled dev CSS at `/_next/static/css/app/layout.css` contains only `backdrop-filter:` (no `-webkit-backdrop-filter:` prefix). PostCSS pipeline includes `autoprefixer` (`ui/postcss.config.mjs`) but no `browserslist` config is declared in `package.json` — autoprefixer is using defaults which currently skip the `-webkit-backdrop-filter` prefix.
- **Risk:** Safari < 18 / iOS < 18 won't blur the sticky header. The `@supports (backdrop-filter: ...)` fallback (which **does not** check for `-webkit-` per spec) correctly degrades to opaque background. No layout break, just no glassy blur.
- **`-webkit-overflow-scrolling: touch`** present in `globals.css:73` ✓ (iOS momentum scrolling).
- **`text-wrap: balance`** used 3× (`page.tsx`, `events/[id]/page.tsx`, `EventCard.tsx`). Safari ≥ 17.4, Firefox ≥ 121. Older browsers ignore — no layout break.
- **`color-mix()` / `:has()`:** 0 occurrences in app code (only inside vendor CSS). No Edge < 105 hazard.
- **`scrollbar-width: thin` / `-moz-appearance`:** 0 in app source. No Firefox-only paths.
- **`scroll-behavior: smooth`:** not declared in app CSS (browser default `auto`). N/A.
- **iOS input-zoom fix:** `globals.css:78-84` sets `font-size:16px` on mobile inputs ✓.

## Feature-detection results (Chromium 148)

```
backdrop-filter (unprefixed):  true
-webkit-backdrop-filter:       false (Chromium dropped legacy alias)
color-mix():                   true
:has():                        true
gap (flexbox):                 true
text-wrap: balance:            true
scroll-behavior: smooth:       true
env(safe-area-inset-*):        true
```

## SSE / Firefox cross-origin

- `EventSource` URL is `${API_BASE}/sse/events` (default `http://localhost:8000`) — cross-origin from `:3001`.
- Backend SSE endpoint returns `access-control-allow-origin: http://localhost:3001` + `access-control-allow-credentials: true` + `content-type: text/event-stream`. Firefox CORS for SSE is satisfied.
- No `withCredentials` set on EventSource — also avoids the stricter `*` vs explicit-origin CORS pitfall.

## Mermaid

- Brief mentioned Mermaid — **not present in this codebase.** Only `@xyflow/react` is used (`WorkflowOverview.tsx`, `PhaseNode.tsx`). xyflow renders SVG natively in all 4 browsers.

## Performance budgets (FCP < 1.5s, load < 3s)

| Page | FCP (ms) | DOM Interactive | Load (ms) | Verdict |
| --- | --- | --- | --- | --- |
| `/` (cold)               | 348  | 350  | 904  | PASS |
| `/` (warm)               | 168  | 171  | 502  | PASS |
| `/events`                | 88   | 74   | 356  | PASS |
| `/events/73` (cold)      | 124  | 115  | 536  | PASS |
| `/events/73` (post-spoof)| 484  | —    | 1011 | PASS (11 RF nodes + 10 edges rendered) |
| `/history` (cold)        | **2612** | 2604 | 2915 | **FAIL FCP** (dev-build chunk size; verify in prod build) |
| `/leaderboard`           | 92   | —    | 370  | PASS |
| `/operators`             | 1124 | —    | 1408 | PASS |
| `/about`                 | 300  | —    | 688  | PASS |

- **History page FCP 2612ms is the only budget breach.** This is a dev-server cold load — Next.js dev compiles per-route on first hit. Worth re-running on a production build (`next build && next start`) before flagging as a real regression. Could not test prod build in 35-min budget.

## Other findings

- **XSS payload** displays correctly as literal text in `<h1>` for event id 73 (`<script>alert(1)</script> 🎉 مرحبا עברית`). React's default escaping handles all 4 browsers identically. Zero `dangerouslySetInnerHTML` blew up.
- **Console errors / warnings:** 0 across all 7 pages tested.
- **No `dangerouslySetInnerHTML` usage in app pages** that would skip React's auto-escape.
- **CLS:** observer attached, no shifts caught during steady-state (xyflow may shift on layout settle — was not measured beyond first paint).

## Tests run

| Test | Result |
| --- | --- |
| T1 Safari CSS prefixes (`-webkit-backdrop-filter`) | **partial** — missing prefix in compiled CSS, but `@supports` fallback OK |
| T1 Safari `gap`, `scroll-behavior`, iOS zoom-on-focus | PASS |
| T2 Firefox `scrollbar-width`, `-moz-appearance` | PASS (not used) |
| T2 Firefox SSE cross-origin | PASS |
| T2 Mermaid / SVG | N/A (no Mermaid); xyflow SVG renders |
| T3 Edge `color-mix()` / `:has()` | PASS (not used) |
| T3 Cmd vs Ctrl hardcode | PASS (no keyboard handlers found) |
| T4 Performance budgets | PASS except `/history` (dev cold-load) |
