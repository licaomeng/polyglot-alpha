# G2 — Keyboard / a11y / Viewport Audit

Date: 2026-05-26
Scope: keyboard navigation, screen-reader friendliness, tiny + huge viewport behavior, pixel polish.
Method: Playwright MCP against `http://localhost:3001/`, read-only.

---

## Top-line verdicts

| Area | Verdict | Notes |
|---|---|---|
| Keyboard navigation | **PASS w/ caveats** | All 11 phase cards, header nav, controls reachable. Skip link present. |
| Tab traps / unreachable elements | **0 traps** | But 10 react-flow EDGE handles are also focusable → tab noise |
| WCAG AA contrast (text) | **~PASS** | All "fail" findings were false-positives from Tailwind `bg/15` alpha; composited contrast is 8-17:1 |
| 320×568 viewport | **PASS** | No body h-scroll on `/`, `/events`, `/operators`, `/events/{id}`. Inner overflows on react-flow are clipped (expected). |
| 768×1024 viewport | **PASS** | SiteHeader fits, nav links visible, no h-scroll. |
| 1024×600 viewport | **PASS** | Layouts hold. |
| 3840×2160 (4K) viewport | **FAIL** | Content stuck at `.container` max-width = 1400px (~36% of canvas). Hero + cards float in vast empty space; react-flow grows only to 1624px. |
| Pixel polish | **MED issues** | Card border-radius mixes 10px / 12px; 3 distinct cyan family hues; 8 distinct border-style buckets on cards. |

---

## Part 1 — Keyboard navigation

### Homepage `/`
- 39 focusable elements. Tab order is logical: skip-link → logo → nav (Overview/Events/History/Leaderboard/Operators/About) → CTAs ("Explore live events", "Trigger live demo", "Leaderboard") → 11 STEP cards → React Flow controls (Zoom In/Out/Fit) → featured event cards.
- **Skip-to-main-content link exists** and is reachable on first Tab (`<a href="#main-content">` with `sr-only focus:not-sr-only`).
- Focus rings present on all 25 tested elements:
  - Header/footer nav uses default `outline: rgb(153,200,255) auto 1px` (Chrome default).
  - Buttons / primary CTAs use a custom `box-shadow` ring: white inner + electric-cyan outer (`rgb(26,240,255)`). **Looks great.**
  - STEP cards have `outline-style: none` and `box-shadow: none` when focused → **NIT: focus invisible on the 11 pipeline cards on the homepage** (they only show focus via opacity/scale transitions). WCAG 2.4.7 "Focus Visible" — borderline.

### Event detail `/events/{id}`
- 72 focusable elements; 13 headings (`h1 → h2 → h3` ordering, no skipped levels).
- All 11 phase cards have **proper aria-label**: e.g. `"Phase 1: Event Ingest, status completed. Activate to jump to the timeline."` — Enter scrolls to the corresponding timeline card.
- **HIGH: 10 react-flow EDGE elements are `tabindex=0` with role="group"** (`aria-label="Edge from ingest to preproc"` …). Keyboard users have to Tab through 10 useless edge announcements between the DAG and timeline. Recommend setting `tabIndex={-1}` on react-flow edges (`nodesFocusable` / `edgesFocusable` props).
- JudgePanel + TrustIndicators: not separately tab-stops; their info icons are not focusable, so the hover-only tooltips are not keyboard-accessible. **MED: hover-only `(i)` tooltips fail WCAG 2.1.1.**
- `aria-live="assertive"` is set on a single `<div>` — SSE phase updates would fire as *interruptions*. **MED: should be `aria-live="polite"`** for non-emergency UI state changes (WCAG 4.1.3).

### Reload (Cmd-R) preserves state
- `/events/70` reload → still on `/events/70`. SSE re-connects. ✓
- Caveat: while the live demo is running on the **homepage**, `TriggerButton.tsx:58/90/94` auto-`router.push()`'s to the newest event. During testing this fired ~5× — every time I `browser_evaluate()`'d, the resulting re-render triggered an SSE event that pushed the route. Probably fine for demo flow, but **annoying for keyboard users sitting on home**: an unexpected redirect is a WCAG 3.2.5 "Change on Request" concern.

---

## Part 2 — Screen-reader simulation

| Check | Result |
|---|---|
| Every interactive element has accessible name | **PASS** (0 unnamed on `/`, 0 on `/events/{id}`) |
| Heading levels sensible | **PASS** — H1→H2→H3, no skips on either page tested |
| Status badges have aria-label | **PASS** — `"Status: Settled (SUBMITTED)"`, `"Mock data"`, `"Phase 1: …"` all carry proper labels |
| `<table>` proper `<th scope>` | Not inspected (no semantic `<table>` on /events/{id}; the auction-bid table uses `<table>` — quick spot-check: it has `<th>` for AGENT/BID/REP but no explicit `scope="col"`) — **LOW** |
| SSE live updates use `aria-live="polite"` | **FAIL** — uses `assertive` (one live region observed). Should be `polite`. |
| SVG icons named | Mostly decorative; 89 SVGs on `/events/{id}` — only the badge dots and pipeline phase nodes within react-flow carry aria-label. Loose SVGs (info dots) lack `role="img"` + title. **LOW** |

---

## Part 3 — Viewport edge cases

Screenshots: `outputs/G2_screenshots/{viewport}_{page}.png`

### 320×568 (iPhone SE)
| Page | h-scroll? | Notes |
|---|---|---|
| `/` | NO | Stacks cleanly. Header nav becomes scrollable horizontal strip (links are inside an overflow-x container; `right > 320px` on individual links is OK since parent clips). |
| `/events` | NO (but page redirected to `/` on first visit — see Note 1 below) | OK |
| `/events/{id}` | NO at root, but react-flow internal overflow is clipped (expected). Phase cards inside DAG are 482px wide → only one visible at a time, hidden behind the dragable canvas. **Minor**: small touch targets in Mock badge area cluster vertically. |
| `/operators` | NO | OK |

**Note 1 — `/events` index page**: when navigating to `http://localhost:3001/events` directly, the page sometimes redirected to `/` (TriggerButton SSE side-effect — see §Notes). Repro: open `/events`, wait 1-2 seconds; redirect fires. The events list is currently being delivered on `/` (via "Featured events" section).

### 768×1024 (iPad portrait)
- SiteHeader: 57px tall, 6 nav links visible. No truncation. **OK.** (D3's fix verified.)
- `/events/{id}`: DAG renders fine, timeline cards stack. No h-scroll.
- All pages PASS.

### 1024×600 (small laptop)
- All pages PASS.
- `/events/{id}`: DAG + timeline both fit. Tight vertical room but expected.

### 3840×2160 (4K)
**This is the big issue.**

- `.container` is hardcoded to **1400px max-width**. Hero H1 sits 1261px from the left edge with 1811px of empty space on the right.
- The master pipeline diagram (`.react-flow__viewport`) **does** grow to 1624px (44% of canvas), but is then dwarfed by the whitespace around it.
- Featured events grid: 3 cards × ~440px wide = 1320px out of 3840px. Cards do not enlarge.
- Cards on `/events/{id}` — timeline & 11-judge breakdown — all capped at 1400px container width.

**Severity: HIGH for big-screen demo recording.** If the recording machine is 4K, the diagram will look tiny and lost. Options:
1. Bump container max-width to `min(95vw, 1920px)` on 2xl breakpoint.
2. Allow the master diagram + timeline to expand beyond `.container` on `>=1920px` breakpoint.

---

## Part 4 — Pixel polish (1920×1080, `/events/{id}`)

### Border / radius inconsistencies
8 distinct (border-width × border-color × border-radius) buckets on cards:

| Border | Radius | Count |
|---|---|---|
| 1px rgba(16,185,129,0.4) [emerald/40] | 12px | 11 |
| 1px transparent | 10px | 10 |
| 0px rgb(19,29,52) | 10px | 8 |
| 1px rgba(19,29,52,0.4) | 10px | 8 |
| 1px rgba(19,29,52,0.6) | 10px | 7 |
| 1px rgba(16,185,129,0.3) | 12px | 4 |
| 1px rgb(19,29,52) | 10px | 4 |
| 1px rgba(16,185,129,0.3) | 10px | 2 |

→ **NIT**: emerald-bordered cards are `rounded-xl` (12px) while neutral-bordered cards are `rounded-lg` (10px). Side-by-side it's noticeable. Pick one.

### Color palette
8 colored variants across the entire `/events/{id}` page:
- 3 cyan family: `rgb(26,240,255)` (electric cyan, primary), `rgb(56,189,248)` (sky-400), `rgb(52,211,153)` (emerald-400 — actually emerald, not cyan, but visually in same family).
- 1 amber family: `rgb(251,191,36)`, `rgb(252,211,77)`.
- 1 fuchsia: `rgb(240,171,252)`.
- → Palette is tight and intentional. **NIT**: the `rgb(56,189,248)` sky-400 appears only on a few accents — could be consolidated into electric cyan.

### Font weights
Only 3 weights in use (400 / 500 / 600). **Excellent** — no random bold scattering.

### WCAG AA contrast (alpha-composited)
Re-verified the worst "fail" cases after compositing `bg/15` alphas over body `#070A13`:
| Element | Original (naïve) | After composite | Pass |
|---|---|---|---|
| "Settled" badge | 1.32 | **8.48** | ✓ |
| "Closed IP" badge | 1.97 | **11.06** | ✓ |
| "Verdict · PASS" | 1.66 | **16.61** | ✓ |
| "0.3000" (amber) | 1.49 | **13.15** | ✓ |
| "hard gate" (fuchsia) | 1.49 | **10.96** | ✓ |

→ **Real WCAG AA: PASS**. (Initial 8/38 failures were artifacts of my naïve bg detection.)

---

## Top 5 issues to fix before final recording

1. **HIGH — 4K viewport content is stuck at 1400px max-width** (`.container`). On a 4K screen the master pipeline diagram looks tiny in a sea of whitespace. Add a `2xl:` / `3xl:` breakpoint that widens the container or lets the diagram bleed wider. Affects: `1`, `events`, `events/{id}`, `operators` — every page.
2. **MED — `aria-live="assertive"`** on the SSE live region should be `aria-live="polite"`. Assertive interrupts whatever a screen-reader is reading; SSE phase updates are not emergencies.
3. **MED — react-flow edges have `tabindex=0`** on `/events/{id}` (10 edges → 10 useless tab stops with announcements like "Edge from ingest to preproc"). Set `edgesFocusable={false}` on `<ReactFlow>`.
4. **MED — Hover-only `(i)` info tooltips** (judge breakdown, phase explanations) are not keyboard-accessible. Add `tabindex=0` + `aria-describedby` or convert to focusable buttons.
5. **NIT — `/events` index URL sometimes auto-redirects to `/`** because `TriggerButton` SSE handler calls `router.push('/events/${eventId}')` or `/events` on new submissions. Combined with another effect, the user lands somewhere unexpected. WCAG 3.2.5 "Change on Request": navigation should not happen without user action. Gate the auto-redirect behind a state check (`if (pathname === '/' && hasJustTriggered) { ... }`).

### Lesser issues (worth fixing if time)
- Border-radius mix 10px vs 12px on cards; pick one.
- Three cyan/emerald hues — could collapse `sky-400` into `cyan-400`.
- STEP cards on homepage have no visible focus ring (focus invisible — WCAG 2.4.7).
- Auction `<table>` lacks `scope="col"` on `<th>`.

---

## Files written
- `outputs/G2_keyboard_viewport.md` (this report)
- `outputs/G2_screenshots/{320,768,1024,4K,1920}_{home,events,event_detail,operators}.png` (15 PNGs)
