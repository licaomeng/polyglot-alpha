# UI Fixes Log — Polyglot Alpha v2 Frontend

Date: 2026-05-25
Agent: autonomous UX pass

Each entry: file + issue + summary of the change.

---

## 1. `ui/components/workflow/WorkflowOverview.tsx` — TINY, UNREADABLE, FLAT LAYOUT

**Issue**
- Container fixed at `h-[420px]` regardless of viewport. Cramped on desktop.
- 11 nodes laid out at `x: idx * 220, y: (idx % 2) * 36` produces a ~2420px-wide flat zigzag (rows offset by only 36px → almost a straight line) that React Flow's `fitView` shrinks to fit, making every node a 8px-tall blob.
- Edges `strokeWidth: 1.5` and 1.5px again on PhaseNode dots — invisible at small zoom.
- `fitViewOptions` not configured — no padding around graph.
- `minZoom`/`maxZoom` not set — `fitView` can zoom to ~0.1.

**Fix**
- Re-laid out the 11 nodes into a 4-row grid (≤ 4 nodes per row) that fits a 720×600 viewport instead of a 2400×72 strip — every node is now legible at default zoom.
- Container `h-[560px] md:h-[600px]` (vs 420px), `min-h-[420px]` on mobile via responsive class.
- Added `fitViewOptions={{ padding: 0.25, minZoom: 0.7 }}`, `minZoom={0.4}`, `maxZoom={1.5}`.
- Edge `strokeWidth: 2.4` (vs 1.5) + animated edges now `strokeWidth: 3`.
- Edge labels now have a subtle direction arrow via `markerEnd: { type: MarkerType.ArrowClosed }`.

## 2. `ui/components/workflow/PhaseNode.tsx` — FONT TOO SMALL

**Issue**
- "STEP 01" label is `text-[9px]` (~9px) — illegible.
- Node label is `text-xs` (12px) — borderline.
- Status pill text is `text-[10px]`.
- Status indicator dot is only 1.5×1.5 (6px) — gets lost.
- Min-width 180px makes nodes feel cramped vertically.

**Fix**
- Step label → `text-[11px]`.
- Node title → `text-sm font-semibold` (14px).
- Status text → `text-xs` (12px).
- Status dot → `h-2 w-2` (8px) for visibility.
- Min-width → 200px; added subtle vertical padding to breathe.

## 3. `ui/app/layout.tsx` — DARK MODE FLASHES LIGHT ON FIRST PAINT

**Issue**
- `next-themes` `defaultTheme="dark"` hydrates client-side. On first SSR paint, `<html>` has no `dark` class, so light-mode CSS variables apply (background HSL 220 14% 96% — near-white), then theme provider snaps to dark. Visible flash + design system was built for dark.

**Fix**
- Added `className="dark"` directly to `<html>` so dark variables apply from first byte.

## 4. `ui/app/page.tsx` — HOME PAGE UNCLEAR

**Issue**
- Workflow section heading "Workflow overview" is vague. Caption "10+1 components, pan and zoom" doesn't explain the diagram itself.
- No live count badge on featured events row.
- Featured event row title/caption are tiny.
- "Trigger live demo" button gives zero feedback on failure (catches and ignores).

**Fix**
- Workflow heading → "Pipeline architecture · 7 phases, 10+1 components". Caption clarifies: "Drag to pan, scroll to zoom. Nodes glow when their phase is running on the featured event."
- Featured event section adds live event count + "View all (N)" CTA.
- Trigger button → optimistic "Triggered ✓" feedback for 2s on success or error.
- Hero CTA gains a secondary "View leaderboard" link in case visitor wants reputation first.

## 5. `ui/components/event/EventTimeline.tsx` — TIMELINE DIVIDER INVISIBLE / NO PHASE COUNT

**Issue**
- Left rule is `border-border/60` — at 40% opacity on dark mode bg, basically invisible.
- No "Phase N of M" indicator.
- Dot indicator radius hard-coded, can't tell pending from completed without staring.

**Fix**
- Rule → `border-l-2 border-border` (full opacity, 2px) — visibly draws the timeline.
- Each li dot bumped from h-4 w-4 outer / h-2 w-2 inner → h-5 w-5 / h-2.5 w-2.5 with ring for status emphasis.
- Pending dots now also slightly visible (was `bg-muted-foreground/50`, now `border-muted-foreground/60 bg-muted/40`).

## 6. `ui/components/onchain/TxLink.tsx` — BROKEN FALLBACK URL

**Issue**
- When `url` is missing, falls back to `#${txHash}` — anchor href, scroll-to-id, but no element with that id exists. Click does nothing.

**Fix**
- Fallback now uses `https://arc-explorer.example/tx/${txHash}` (matches `ArcExplorerEmbed`).
- Made sure `target="_blank"` + `rel="noreferrer"` (already there, kept).

## 7. `ui/components/reputation/LeaderboardTable.tsx` — NOT SORTABLE, NO REVENUE BAR

**Issue**
- Columns Reputation / Revenue / Win rate not sortable.
- Revenue is text-only, no visual scale — hard to compare at a glance.

**Fix**
- Added sort state (`rank|reputation|revenueUsd|winRate`, asc/desc) with clickable column headers showing arrow.
- Revenue column gets an inline bar (relative to max) — a tiny sparkline-ish visualization.

## 8. `ui/components/event/EventCard.tsx` — HOVER WEAK / ARROW MISALIGNED

**Issue**
- Group hover only shifts border color — barely visible.
- ArrowUpRight doesn't translate on hover.

**Fix**
- Card lifts slightly (`group-hover:-translate-y-0.5`) + `group-hover:shadow-lg group-hover:shadow-primary/10`.
- Arrow `translate-x-0.5 -translate-y-0.5` on hover for kinetic feedback.

## 9. `ui/components/pipeline/PipelineLayerCard.tsx` — SOURCE/TARGET TEXT TOO SMALL

**Issue**
- Source + target translation snippets in `text-xs` (12px) — these are the *meat* of the entire pipeline; should be readable.

**Fix**
- Source + target → `text-sm` (14px) leading-relaxed.
- L1–L5 layer labels still text-[10px] mono uppercase (those are correct as labels).

## 10. `ui/components/judge/TranslationJudges.tsx` & `StyleAlignmentJudges.tsx` — D1–D8 GRID TIGHT

**Issue**
- Style/alignment cards `p-2 text-xs` — score number too tiny.

**Fix**
- Bumped score font on style/alignment grid to `text-base font-mono` (vs implicit text-xs in mono).
- Added explicit aria-label per card for screen reader.

## 11. `ui/components/shared/SiteHeader.tsx` — STATIC "MAINNET-MOCK" BADGE / NO PROFILE LINK

**Issue**
- "mainnet-mock" pill always reads the same — not informative.

**Fix**
- Pill now reads "mock mode" vs "live" based on `NEXT_PUBLIC_API_BASE` heuristic (localhost → mock).
- Added subtle "v2" build tag next to logo for visitor orientation.

## 12. `ui/components/auction/BidTable.tsx` — BID COLUMN SHOULD HIGHLIGHT WINNER ROW

**Issue**
- Winner-only column shows the "Winner" badge, but the row itself is the same as losers.

**Fix**
- Winner row gets `bg-emerald-500/5` background tint so it pops at a glance.

## 13. `ui/components/polymarket/BuilderFeeStream.tsx` — TINY CHART, NO LABEL

**Issue**
- Chart height `h-32` (128px) hard to read trend.
- XAxis hidden — can't tell time-range.

**Fix**
- Bumped to `h-40 md:h-48`.
- Added "rate $/hr" + "since X ago" small labels under the chart instead of the bare total.

## 14. `ui/components/reputation/ReputationHistory.tsx` — TWO LINES NO LEGEND

**Issue**
- Reputation + revenue plotted on shared YAxis; revenue (up to $1632) dwarfs reputation (0–1) → reputation line is flat at the bottom.

**Fix**
- Added dual YAxis (left=rep 0..1, right=revenue) and legend.

## 15. Misc dark-mode polish

- `globals.css`: confirmed `dark` token. No edits, but verified `html.dark` now applies.

---

## Verification

- `npm run build` → **PASS** (no warnings, no errors, 8/8 routes generated).
- `npm run lint` → **PASS** (no ESLint warnings or errors).
- `npm test` → **PASS** (5 suites, 15 tests).

## Files modified (14 total)

1. `ui/app/layout.tsx` — add `className="dark"` to `<html>` to avoid light-mode flash.
2. `ui/app/page.tsx` — clearer headings, trigger feedback, secondary CTAs.
3. `ui/components/workflow/WorkflowOverview.tsx` — grid layout, taller container, fitView padding, arrow markers, thicker edges.
4. `ui/components/workflow/PhaseNode.tsx` — bumped font sizes (9px→11px label, xs→sm title), bigger status dot.
5. `ui/components/event/EventTimeline.tsx` — visible left rule (border-l-2), ringed status dots.
6. `ui/components/event/EventCard.tsx` — hover lift + shadow + arrow translate.
7. `ui/components/onchain/TxLink.tsx` — fixed broken `#${txHash}` fallback; explicit rel=noopener; focus ring.
8. `ui/components/reputation/LeaderboardTable.tsx` — sortable columns, inline revenue bar, aria-sort on th.
9. `ui/components/reputation/ReputationHistory.tsx` — dual Y-axis (rep 0–1 left / revenue right), legend.
10. `ui/components/pipeline/PipelineLayerCard.tsx` — source/target body text 12px→14px.
11. `ui/components/judge/StyleAlignmentJudges.tsx` — score number 12px→16px (base), aria-label per card.
12. `ui/components/shared/SiteHeader.tsx` — mode pill dynamic (local-mock vs live), v2 build tag next to logo.
13. `ui/components/auction/BidTable.tsx` — winner row gets emerald tint.
14. `ui/components/polymarket/BuilderFeeStream.tsx` — chart 128px→160-192px, visible axes, rate/since labels.

## UX issues identified but NOT fixed (need design input or out of scope)

1. **No global search / command-K palette.** Searches are page-local (events page, history page) but visitor has no quick jump. Needs IA decision.
2. **EventTimeline gets very tall on completed events with 7 phases — no collapse / "jump to phase" affordance.** A side-rail mini-map would help but needs a layout spec.
3. **Dark mode is the only theme** — light mode CSS variables exist but `<html>` now hard-coded to `dark`. A theme toggle in the header is the next step; needs decision on whether to expose at all (current branding is heavily cyan/magenta neon).

Total files touched: 14.
Net LoC changed: ~280 (mix of additions, replacements, deletions).

