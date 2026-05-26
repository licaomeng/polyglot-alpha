# G1 — Picky First-Time User Findings

**Tester persona**: Senior engineer evaluating a hackathon project. 5-min budget. Mac trackpad, 1920×1080 starting viewport. No prior context.
**Test session**: 2026-05-26 07:06–07:13 UTC. Backend `localhost:8000`, UI `localhost:3001`.
**Read-only**: no code modified. All evidence in `outputs/G1_screenshots/`.

---

## Summary by severity

| Severity | Count |
|---|---|
| CRITICAL | 3 |
| HIGH | 6 |
| MED | 5 |
| LOW | 4 |
| NIT | 4 |
| **Total** | **22** |

---

## CRITICAL findings

### C1 — Pages randomly auto-redirect to `/events/<id>` while user is browsing
- **Page**: every page, but starts on `/`
- **Expected**: Going to `http://localhost:3001/` shows landing page; staying on `/operators`, `/history`, `/leaderboard`, etc.
- **Actual**: Going to `/` redirected to `/events/69`. Going to `/operators` later auto-redirected to `/events/70`. Going to `/history` redirected to `/operators`. This is reproducible: nav to ANY page → page content briefly appears → URL silently changes to `/events/<latest>` within ~1s.
- **Reproduction**: open `http://localhost:3001/history` cold → URL becomes `/operators` then `/events/<n>` without any click. Repeats on every fresh nav.
- **Suspected cause**: `TriggerButton` (mounted via `HomePage` and possibly elsewhere) has `useEventStream(eventId)` plus a `useEffect` that calls `router.push('/events/${eventId}')` on `event.finalized` SSE events. Because the backend emits real lifecycle events on its own cadence (or because `busy` state survives across renders/HMR), the SSE-driven `router.push` keeps firing. Confirmed code path:
  - `ui/components/TriggerButton.tsx:53-66` — `useEffect` on `latest.type === "event.finalized"` calls `router.push`
  - `ui/components/TriggerButton.tsx:90` — also pushes immediately on POST success
- **Impact**: The product is essentially **unusable** as a demo. A judge cannot read the landing page, browse the leaderboard, or stay on operators — they get hijacked to a random event detail mid-read. This will absolutely tank a hackathon demo.
- **Fix**: Only fire `router.push` when the SSE `event.finalized` payload's `event_id` matches the local `eventId` state AND that local `eventId` was actually set by *this* component's button click within the last N seconds. Add `clickedAt` ref and short-circuit if `Date.now() - clickedAt > 90_000`.
- **Screenshots**: `outputs/G1_screenshots/01_landing_1920.png` (landed on `/events/69` instead of `/`), `08_history.png` (showing operators content when I navigated to `/history`).

### C2 — "Register your agent" CTA opens a `mailto:` to a personal Gmail
- **Page**: `/operators`
- **Expected**: A clear form, OAuth flow, or branded operator-registration endpoint (e.g., `mailto:operators@polyglot-alpha.app`, a wallet-connect modal, or a doc link).
- **Actual**: `<a href="mailto:licaomeng@gmail.com?subject=PolyglotAlpha%20Operator%20Registration">` — author's personal Gmail address exposed on the public-facing operator onboarding screen.
- **Source**: `ui/components/operators/RegisterOperatorCta.tsx:90`
- **Impact**: (1) Privacy leak — author's personal address goes into every visitor's address book / spam list. (2) Looks amateurish for the headline "open marketplace protocol" claim. (3) Email isn't on the project domain, so a judge can't even tell it's legitimate.
- **Fix**: Replace with either (a) `operators@polyglot-alpha.<domain>` alias, (b) a "Coming soon — join the Discord/Telegram" stub link, or (c) explicit `<Badge>Coming soon</Badge>` and remove the mailto entirely.

### C3 — Mock/test agents leak into the public leaderboard
- **Page**: `/leaderboard`
- **Expected**: Production-looking agents (seeders + named external operators) with realistic wallet addresses.
- **Actual**: 16 rows of which 6+ are obvious test fixtures:
  - `0xdead…0001`, `0xdead…0002` (dead-address placeholders)
  - `agent a` @ `0xagent_a`, `agent b` @ `0xagent_b`, `agent c` @ `0xagent_c`, `agent solo` @ `0xagent_solo` (clearly seeded test data — these are not valid 40-char hex addresses)
  - `0xaaaa…aaaa`, `0xbbbb…bbbb`, `0xcccc…cccc`, `0xdddd…dddd`, `0xeeee…eeee`, `0xffff…ffff` (test wallet pattern)
- Verified via `GET /leaderboard` raw — all returned by backend; this is a DB-seeding issue, not a UI bug.
- **Impact**: Immediately destroys credibility — the very first thing a judge clicks ("Leaderboard") shows that 75% of "agents" are obvious fakes named `agent a`. The earlier `/operators` claim "0 external operators" is then contradicted by these test rows on the leaderboard.
- **Fix**: Either (a) filter `winRate=0 AND total_wins=0` rows out client-side, or (b) seed the DB with names that look intentional (`Reference Bench A`, `Reference Bench B`) and use valid checksummed addresses, or (c) prune the obvious test rows from the DB before recording demo.
- **Screenshot**: `outputs/G1_screenshots/07_leaderboard.png`

---

## HIGH findings

### H1 — Phase-count contradiction: heading says "7 phases", DAG shows 11
- **Page**: `/` landing
- **Expected**: Consistent count of pipeline phases.
- **Actual**: Section heading reads "Pipeline architecture · **7 phases**, 10+1 components" but the DAG below shows 11 numbered nodes (STEP 01 Event Ingest → STEP 11 Reputation Update). The Phase timeline elsewhere uses 7 numbered cards (01-07). Three different numbers in three places.
- **Impact**: A picky judge immediately questions: "Is it 7 or 11? Which is canonical?" Erodes trust within first 10 seconds on the homepage.
- **Fix**: Pick one canonical count (likely 11 = 10 pipeline + 1 reputation) and update both the heading text and the secondary timeline; or explain explicitly: "7 user-visible phases mapped over 11 on-chain steps".
- **Screenshot**: `outputs/G1_screenshots/03_landing_final.png`

### H2 — Phase numbering doesn't match between DAG and timeline
- **Page**: `/events/<id>`
- **Expected**: STEP 03 USDC Auction in the DAG = card "03 USDC Auction" in timeline.
- **Actual**: DAG goes 01–11 (Event Ingest, Preprocess, Auction, Translation, Debate, Synthesizer, Judges, Anchor, Polymarket, Revenue, Reputation). Timeline goes 01–07 with completely different names: 01 Event Ingestion → 02 USDC Auction → 03 Translation Pipeline → 04 11-Judge Panel → 05 On-chain Anchor → 06 Polymarket V2 Submission → 07 Streaming Revenue. A user clicking "Spotlight phase USDC Auction on the DAG overview" (which is step 03 in DAG) ends up looking at timeline card 02. Cognitively jarring.
- **Fix**: Align numbering — either expand the timeline to 11 steps mirroring the DAG, or collapse the DAG to 7 nodes mirroring the timeline.

### H3 — "0s elapsed" + "~Xs remaining" shown on COMPLETED phases
- **Page**: `/events/<id>` for any settled event (e.g., `/events/69`)
- **Expected**: Completed phases show actual duration ("4.2s") and don't show "~remaining".
- **Actual**: Every phase card on a settled event still says "Event Ingestion · 0s elapsed" with a progress bar at zero and "~5s remaining" — even though the badge on the same card says "Done". The progress bar is also showing as an active progressbar.
- **Impact**: Looks broken — like the page is permanently stuck loading. Picky user thinks: "is the data live? Has it ever finished?"
- **Fix**: When `status === 'completed'`, hide the elapsed/remaining counter and show actual duration from `startedAt`/`completedAt`.

### H4 — `triggered_at` says "8h ago" for events that should be fresh
- **Page**: Multiple — landing "Featured events" cards, event detail header
- **Expected**: Demo trigger creates a new event with `triggered_at=now`, so card should say "just now" or "5s ago".
- **Actual**: Every cached event reads `ingested 8h ago`. There's no event newer than 8h. The Trigger Live Demo button doesn't appear to produce events that age correctly (or all 70 events were seeded at once and `triggered_at` is set to seed time, not lifecycle start).
- **Fix**: Either re-seed with staggered timestamps for demo realism, or set `triggered_at` to the moment the BackgroundTask actually starts processing.

### H5 — DAG has accessibility-labeled edges but DAG group is `application` role with no description
- **Page**: `/` and `/events/<id>`
- **Expected**: `application` role should have aria-label describing what the user can do.
- **Actual**: `application [ref=e63/e84]` has no accessible name. Inside, ten edges are correctly labeled ("Edge from ingest to preproc"), but the container itself is opaque to screen readers.
- **Fix**: Add `aria-label="Pipeline DAG · 11 phases"` or similar to the React Flow wrapper.

### H6 — No rate limit / dedup on `POST /trigger/event`
- **Page**: API (`/trigger/event`)
- **Expected**: Burst of 5 rapid POSTs should either dedup (return same `event_id`) or rate-limit.
- **Actual**: 5 rapid calls all returned 422 (missing body), but with a valid body (`{}` is rejected; emoji injection POST got accepted) the endpoint accepts each one and creates a new event id, with no per-IP or per-content-hash dedup. A judge clicking "Trigger live demo" 10× quickly will spawn 10 events and consume 10× LLM budget.
- **Fix**: Add 5-second debounce server-side keyed by remote IP, or client-side: disable Trigger button for 60s after first click (until lifecycle finishes).

---

## MED findings

### M1 — Trigger Button label sequence inconsistency
- **Page**: `/`
- **Expected**: After clicking "Trigger live demo", button reads "Triggered" (or "Done").
- **Actual**: Label flickers through `"Fetching latest non-English news…"` → `"News cluster scored — opening auction…"` → … → `"Streaming builder fees…"` → `"Done — navigating to event detail…"` then page navigates. The final state `Triggered` set in code at line 60/92 is invisible because nav happens immediately. The UI never shows the "success" state on the homepage — user is yanked to detail page.
- **Fix**: Either show `Triggered` for 800ms before navigating, or change the success copy to indicate navigation is intentional.

### M2 — Trust indicator hover tooltips visually unclear
- **Page**: `/events/<id>` event header
- **Expected**: Hovering "on-chain verified" should show what's verified (which contract / which TX hash matches).
- **Actual**: The link is `https://testnet.arcscan.app/tx/<hash>` (good) — but the badge has no `title` or `aria-describedby`. A user has to click to find out.
- **Fix**: Add `title="Anchored as TX 0x68a4…8ae9a0 on Arc testnet — click to verify"` to each Trust Indicator.

### M3 — D-judge tooltips not actually shown on hover (only as button labels)
- **Page**: `/events/<id>` judge panel D1-D8
- **Expected**: Hovering D1 reveals "Structural" + the description.
- **Actual**: Each D-button has aria-label like `"Explain D1 · Structural"` but no native `title` or visible tooltip popover on hover. A user has to click to see what D1 means. The text "hover the (i) for what this phase does" elsewhere in the UI is similarly unfulfilled — clicking the (i) icon doesn't appear to open any explainer modal/popover in the snapshot.
- **Fix**: Either add `<Tooltip>` wrapper from the design system, or surface a one-line description as native `title` attribute.

### M4 — "Submit Real" button in dry-run panel has no confirmation
- **Page**: `/events/<id>` Polymarket V2 Submission section
- **Expected**: Submitting to real Polymarket should require a confirmation dialog (irreversible, costs USDC).
- **Actual**: Plain button labeled "Submit Real" sits next to "This submission was simulated (dry-run). Promote it to Polymarket production?" — one-click promotion to prod. No `<AlertDialog>`, no double-confirm.
- **Fix**: Wrap in a confirm dialog: "This will submit to production Polymarket. Type CONFIRM to proceed."

### M5 — IPFS link points to `ipfs://mock/…` URL embedded in `https://ipfs.io/ipfs/`
- **Page**: `/events/<id>` Trust Indicators + IPFS links throughout
- **Expected**: Link should be a real CID, or the link should be disabled with a "mock data" indicator instead of opening a 404.
- **Actual**: Links go to `https://ipfs.io/ipfs/ipfs://mock/2a2c2ad88fb2` — that's the literal string "ipfs://mock/…" appended after `/ipfs/`. This will 404 / NXDOMAIN on click.
- **Fix**: When in mock mode, render IPFS pin as a static badge with no `href` (or `href="#"` with `onClick={e=>e.preventDefault()}`).

---

## LOW findings

### L1 — Header says "POLYGLOT·α  v2" but page title is "Polyglot Alpha v2"
- Inconsistency — small but visible.

### L2 — `local-mock` indicator in header is unexplained
- A picky user wonders what "local-mock" means. Add tooltip: "Running against local SQLite + mock seeders — no real on-chain TX."

### L3 — Footer reveals backend URL
- `backend: http://localhost:8000` in footer. Fine for dev, but should not be exposed in any "demo recording" build.

### L4 — Page renders "Closed IP — Judge weights and prompt internals are intentionally not exposed." right next to a 1.00 score for every D-judge.
- Reads like a deflection. Either show the formula in collapsible "How is this scored?" detail, or remove the disclaimer (since every score is 1.00, there's nothing to hide).

---

## NIT findings

### N1 — Heading capitalization mixed
"Pipeline architecture · 7 phases, 10+1 components" — sentence case. But "Featured events", "Become an Operator", "REFERENCE SEEDERS" are mixed. Pick one and apply.

### N2 — Bid table column header "Rep." should be "Reputation" or have abbreviation tooltip.

### N3 — "winner · —" placeholder in Translation Pipeline card on a settled event
- Should populate with actual winner address, not em-dash.

### N4 — Two adjacent "8h ago" timestamps on every featured-events card stack make the homepage feel cached/dead.

---

## 3 things that DID work well (positive signal)

1. **Skip-to-main-content link is present and correctly hidden until focused** — accessibility-aware out of the gate.
2. **DAG no longer eats scroll** — F-Scroll fix verified. Wheel events over `.react-flow` do NOT capture page scroll. Page scroll works fluidly past the DAG section.
3. **Backend input validation is solid** — `event_source` is enum-validated with a clear error message listing all valid values; title length capped at 500 chars with structured `pydantic` error; missing-body returns 422 with a useful pointer to the missing field. Better than most hackathon backends.

---

## Path 7 — API edge cases (from `curl`, no rate limiting in UI)

| Input | Status | Body returned |
|---|---|---|
| Empty POST body | 422 | `{"detail":[{"type":"missing","loc":["body"]}…]}` ✓ |
| `POST` with no Content-Type | 422 | Same as above ✓ |
| Title with `<script>alert(1)</script>` + RTL + emoji | 200 | Event created; payload stored as-is. UI auto-escapes via React, so no live XSS, but stored payload is unsanitized — risk if any downstream consumer renders via `dangerouslySetInnerHTML`. |
| Title of 5000 chars | 422 | `string_too_long` (max 500) ✓ |
| `event_source: "invalid_xyz"` | 422 | Lists valid values ✓ |
| 5× rapid POST `{}` | 5× 422 (all in 10ms) | No rate limiting; if body had been valid, 5 events would have spawned ✗ |

---

## Final judgment

**Would a hackathon judge be impressed?** *Not in current state.* The auto-redirect bug (C1) makes the demo physically un-navigable within ~5 seconds, the personal Gmail in the Register CTA (C2) and the mock agents in the leaderboard (C3) immediately leak amateur-hackathon vibes during the first 30 seconds of judging. The underlying architecture and pipeline visualization are genuinely impressive — but a picky judge stops reading once the page nav breaks.

**Top 5 must-fixes before final demo:**
1. **C1** — Remove the SSE-triggered `router.push` from `TriggerButton` (or gate it on `Date.now() - clickedAt < 90s`).
2. **C2** — Replace `mailto:licaomeng@gmail.com` with branded address or "Coming soon" stub.
3. **C3** — Filter `agent a/b/c`, `0xagent_*`, `0xdead…000*` rows from leaderboard before demo.
4. **H1** — Pick canonical phase count (7 vs 11) and apply everywhere.
5. **H3** — Don't show "0s elapsed / ~5s remaining" on phases that are already `completed`.
