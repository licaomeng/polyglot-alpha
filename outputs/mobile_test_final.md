# Mobile Viewport Deep Test — PolyglotAlpha v2

Tested 7 pages across iPhone SE (375x667) and iPhone 11 (414x896) — 14 viewport+page combinations.

## Critical mobile bugs found

| # | Bug | Page(s) | Severity |
|---|---|---|---|
| 1 | Filter row (search + 5 buttons) overflowed viewport horizontally — body.scrollWidth 617px on 375px screen | /events | High |
| 2 | Filter row (search + select + CSV) overflowed horizontally | /history | High |
| 3 | Leaderboard table card was 451px wide on 375px viewport (grid item didn't shrink) | /leaderboard | High |
| 4 | Phase-header buttons were 28px tall, below the 44px touch guideline | /events/{id} | Medium |
| 5 | Primary CTA buttons (hero) were 40px tall, miss the 44px guideline | / (and most pages) | Medium |
| 6 | TxLink anchor inline-text, ~14px hit height | /events/{id} | Medium |
| 7 | No `touch-action: manipulation` — 300ms tap delay on iOS Safari | global | Low |
| 8 | No safe-area-inset support — sticky header collides with notch | global | Low |
| 9 | Inputs would trigger iOS auto-zoom because text < 16px | /events, /history | Low |
| 10 | Footer backend URL could overflow on narrow screens | global | Low |

## Touch target compliance (44x44 strict)

| Page | Before | After |
|---|---|---|
| / | 47% | 81% |
| /events/{id} | 38% | 81% |
| /leaderboard | 17% | ~75% (held back by table sort buttons at 40px height) |

Remaining shortfalls are the 3 ReactFlow zoom-control buttons (26x26, third-party) and a small number of table sort buttons at 40px. Both leave 4–5 truly out-of-spec interactive elements per page, far below the original 9–25.

## Horizontal-overflow audit

After fixes, all 7 pages × both viewports report `body.scrollWidth == window.innerWidth` (no horizontal scroll). `html { overflow-x: hidden }` is the safety net for nested tables that exceed natural width — they now scroll internally via the existing `<div className="overflow-auto">` wrapper.

## 5 most impactful mobile improvements made

1. **`ui/components/ui/button.tsx`** — Bumped default/sm/lg/icon sizes to `min-h-[44px]` (and `min-w-[44px]` for icon) on phones, restoring the desktop 36–40px scale at the `sm:` breakpoint. This single change took touch compliance from ~47% to 75–81% across every page.
2. **`ui/app/events/page.tsx` + `ui/app/history/page.tsx`** — Filter rows now stack vertically on mobile (`flex-col gap-2 sm:flex-row`). On `/events`, the 5 status buttons live in a horizontal scroll strip so the user can pan through them with no overflow.
3. **`ui/app/globals.css`** — Added `overflow-x: hidden` on html/body, `touch-action: manipulation` on body, `-webkit-overflow-scrolling: touch` on overflow containers, env safe-area-inset paddings on header/footer/body, and a 16px minimum input font on phones to defeat iOS auto-zoom.
4. **`ui/app/leaderboard/page.tsx`** — Added `min-w-0 overflow-hidden` to the grid cells holding the table/chart cards. CSS grid items default to `min-width: auto`, so the table was forcing the parent grid wider than viewport — now the inner `Table` scrollable wrapper does its job and the page stays at 375 px.
5. **`ui/components/event/PhaseCard.tsx` + `TxLink.tsx`** — Phase header buttons went from 28px to 44px tall, and TxLink anchors get a `min-h-[44px] px-2 py-1` hit area on phones while staying inline on desktop.

## Recommended mobile-first next steps

1. **Replace ReactFlow's tiny zoom controls with custom 44x44 buttons** (the !important CSS override didn't always win — Controls might re-render outside our sheet).
2. **Add a mobile hamburger menu** — the primary nav is currently a horizontal scroll strip; better to collapse to a `<details>` or sheet under 640px so the brand + status pill have breathing room.
3. **Add a "scroll for more →" hint on the bid table and history table** so users see they're scrollable on phones.
4. **Audit `framer-motion` height animations on PhaseCard** — the AnimatePresence with `height: 'auto'` can briefly jank on low-end Android because it re-measures every frame. Consider `LazyMotion` and `domAnimation` for a smaller bundle on phones.
5. **Add a `viewport-fit=cover` meta to lock notch handling**, e.g. `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />`. Without it the env() insets resolve to 0 on PWA shells.

## Files touched

- `ui/app/globals.css`
- `ui/app/events/page.tsx`
- `ui/app/history/page.tsx`
- `ui/app/leaderboard/page.tsx`
- `ui/components/ui/button.tsx`
- `ui/components/event/PhaseCard.tsx`
- `ui/components/onchain/TxLink.tsx`
- `ui/components/reputation/LeaderboardTable.tsx`
- `ui/components/shared/SiteHeader.tsx`
- `ui/components/shared/SiteFooter.tsx`

## Artifacts

- `outputs/mobile_test_iter_1.json` — per-page numeric results
- `outputs/screenshots/mobile_375_*.png` (7 pages, plus 1 "events_before" baseline)
- `outputs/screenshots/mobile_414_*.png` (7 pages)
