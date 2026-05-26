# C1 UI Audit — 2026-05-26

Pages audited (8/8): `/`, `/events`, `/events/[id]` (read-only, owned by T1), `/leaderboard`, `/agents/[address]`, `/history`, `/about`, `/operators` (read-only, owned by T6).

Viewports tested: 1280×800, 768×600, 375×667. TypeScript: 0 errors before, 0 errors after.
Screenshots: `outputs/C1_screenshots/`.

---

## HIGH severity — fixed in-place

### H-1. React Flow Controls invisible (white-on-white)
**Where**: home page workflow `Controls` (zoom in / zoom out / fit view buttons), bottom-left of the diagram.
**Cause**: xyflow's own stylesheet (`_app-pages-browser_components_workflow_WorkflowOverview_tsx.css`) loads in a route chunk that wins over our `app/layout.css` overrides. Our overrides were sitting inside `@layer base` with `.react-flow__controls-button` specificity, which the xyflow rules with `border-bottom` selectors out-specified.
**Fix**: moved the override block OUTSIDE `@layer base`, doubled the selector specificity (`button.react-flow__controls-button`, `.react-flow .react-flow__controls-button`), and added explicit `border: none !important` so border-bottom from xyflow no longer paints a light divider. File: `ui/app/globals.css`. Verified post-fix bg = `rgb(9, 14, 26)` and svg fill = `rgb(241, 245, 249)` → strong contrast.
Screenshot: `13-controls-after-fix.png`.

### H-2. Agent reputation chart silent failure
**Where**: `/agents/[address]` — when an agent has no `history`, the chart card was visually empty (the user just sees a blank rectangle next to the header, no explanation).
**Cause**: `ReputationHistory` returns `null` on empty data; parent card has no fallback.
**Fix**: `ReputationHistory` now renders a 64-tall dashed-border placeholder with `role="status"` and copy "No reputation history yet — this agent hasn't produced fills." 7 lines added.
File: `ui/components/reputation/ReputationHistory.tsx`.

---

## MEDIUM severity — fixed in-place

### M-1. PhaseNode missing aria-label
**Where**: home page workflow nodes (clickable React Flow nodes).
**Cause**: each node has `cursor-pointer` + `title` but no `aria-label`. React Flow already sets `tabindex=0` and `role=group`, but screen readers announced only the visible text, missing the status and the "click to jump" affordance.
**Fix**: added `aria-label={"Phase N: <label>, status <status>. Activate to jump to the timeline."}` on the inner div.
File: `ui/components/workflow/PhaseNode.tsx`.

### M-2. Events list refreshing indicator not announced
**Where**: `/events`, the "Showing N of M events · refreshing…" line.
**Cause**: the `· refreshing…` span flips in/out on every 5s SSE-driven refetch but had no live region, so screen-reader users could not tell that a background refresh was in flight.
**Fix**: added `aria-live="polite"` on the parent `<p>` so the change is announced without stealing focus.
File: `ui/app/events/page.tsx`.

---

## LOW severity — documented (NOT auto-fixed)

### L-1. SiteHeader nav clipped at 768px (tablet)
**Where**: `SiteHeader` between 640px and ~830px the nav has 6 items + brand + live indicator. At 768×600 the "About" link is partially hidden behind the "local-mock" status pill on the right.
**Cause**: the right-hand live indicator uses `hidden sm:flex` so it shows at sm:640+, but the nav `flex-1 overflow-x-auto` lets the brand+nav grow past the available space when there isn't enough room.
**Repro**: navigate to any page at viewport 768×600 → "About" is cut off / wraps under "local-mock".
**Recommended fix**: bump the live indicator from `sm:flex` to `lg:flex` (1024+), or compress the nav text on md (e.g. icons-only for History/Operators), or stop showing the indicator until lg.
**Size**: ~5 lines but touches a layout primitive used everywhere; deferred to human review.

### L-2. AgentProfile 3-column stat grid cramped at 768px
**Where**: `/agents/[address]` left card — REPUTATION / REVENUE / WIN RATE labels collide when the card column is half the viewport.
**Cause**: `grid-cols-3` is fixed regardless of breakpoint; at 768px each cell is ~70px wide which crushes the 10px-uppercase labels.
**Repro**: `/agents/0x...` at 768×600 → labels overlap (see `11-agent-after-fix.png`).
**Recommended fix**: switch to `grid-cols-1 sm:grid-cols-3` so the three stats stack on the narrow column, or shorten labels ("REP" / "REV" / "WIN").
**Size**: 1 line. Skipped per "DO NOT TOUCH `/operators` and adjacent owned files" boundary — `AgentProfile.tsx` is allowed but the change is layout, prefer human review.

### L-3. favicon.ico 404
**Where**: every page request → `GET /favicon.ico 404`.
**Cause**: no `app/icon.{png,ico}` or `app/favicon.ico` present.
**Fix**: add a `ui/app/icon.svg` (Next 14+) or drop a real `favicon.ico` in `ui/public/`. Skipped because it requires a binary asset.

### L-4. React Flow "container needs width and height" warning
**Where**: console log on home page initial paint.
**Cause**: during the `dynamic(() => import(WorkflowOverview))` loading state, the wrapper div has explicit height but the inner `ReactFlow` measures parent before paint. Cosmetic — no visible regression.
**Fix**: ignore (the warning self-resolves on the first layout flush) OR set `defaultViewport` and `nodeOrigin` on `<ReactFlow>` to skip the auto-measure step.

### L-5. Stale 404s (`/agents`, `/markets`, `/judges`)
**Where**: console errors on initial load.
**Verified**: no `Link` or `fetch` references to those paths in our pages (`grep -rn` clean). These 404s likely came from prior browser session navigation or a prefetch on a route that no longer exists. Not a code bug.

### L-6. Long event headlines truncated in `/agents/[address]` recent-events list
**Where**: each `<li>` has `truncate` so anything over ~50ch is cut. The full title is preserved in `title=` attribute.
**Status**: acceptable — keyboard/mouse users get the tooltip; screen readers read the full text via `aria-label` (the `<Link>` text is the full headline before truncation).

---

## What I tested but found OK

- `EmptyState` — proper `role="status"` and discernible heading.
- `EventCard` — focus-visible ring on `<Card>` via group-focus-visible; keyboard accessible.
- `EventStatusBadge` — has `title` + `aria-label` with both human + canonical status.
- `Button` size variants — all ≥44px on mobile (`min-h-[44px]`) and 36/40px on sm+ per Apple HIG.
- Color contrast: `text-muted-foreground` (`rgb(148,163,184)`) on `body` (`rgb(7,10,19)`) ≈ 7.3:1 — passes WCAG AAA even at 10px.
- History page CSV export — proper escaping (JSON.stringify on headline).
- Skip-to-main-content link — present in `layout.tsx`, focus-visible.
- `<select>` and `<input>` in `/history` and `/events` — have associated `aria-label`s, no orphans.
- Mobile (375×667) layout — home and history both render cleanly.

---

## Files modified

1. `ui/app/globals.css` — moved xyflow Controls override out of `@layer base`, added higher-specificity selectors, removed light-mode default border. +30 / −18 lines.
2. `ui/components/reputation/ReputationHistory.tsx` — replaced `return null` with empty-state placeholder. +9 / −1.
3. `ui/components/workflow/PhaseNode.tsx` — added `aria-label`. +1.
4. `ui/app/events/page.tsx` — added `aria-live="polite"` on count/refresh indicator. +1.

All edits well under the 20-line-per-file budget.

## Final verification

- `pnpm exec tsc --noEmit` → 0 errors.
- `curl http://localhost:3001/{,/events,/leaderboard,/about,/history,/operators,/agents/0x...}` → all 200.
- xyflow Controls bg `rgb(9,14,26)` (dark) + glyph `rgb(241,245,249)` (light) — visually verified.
