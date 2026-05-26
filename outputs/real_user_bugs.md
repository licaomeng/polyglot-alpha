# Real-User Bug Hunt — PolyglotAlpha v2

**Hunter persona:** non-technical evaluator with 5 minutes to play.
**Session date:** 2026-05-25/26 (overnight)
**Method:** Playwright MCP-driven exploration on `http://localhost:3001`
(Next.js dev) + `http://127.0.0.1:8000` (FastAPI), three personas (impatient /
thorough / adversarial). Working dir `/Users/messili/codebase/polyglot-alpha`.

Severity legend:
- **BLOCKER** — demo evaluator would think the product is broken.
- **ANNOYING** — visible UX wart; demo still works.
- **COSMETIC** — only spotted with DevTools or by hovering edge cases.

---

## BLOCKER · 1. Events page filters silently drop every real row

**Repro:**
1. Open `/events`.
2. Backend returns 50 events; statuses are uppercase canonical
   (`SUBMITTED`, `FAILED`, `EVALUATING`, `REJECTED`, `PENDING`,
   `AUCTION_OPEN`…).
3. Old UI filter chips were `all` / `running` / `completed` / `live` and the
   filter logic was `e.status === filter` (strict equality, lowercase).
4. Click "Running" — see *no rows* even though 7 events are `PENDING` or
   `EVALUATING`. Click "Completed" — see *no rows* even though 26 are
   `SUBMITTED`.

**Why a real evaluator hits this:** every status chip looks broken. The
demo's "click around to see live events" loop dies on the second click.

**Fix:** centralized status taxonomy via `lib/status.ts` (added by another
sub-agent) — events page now imports `STATUS_BUCKETS`, `BUCKET_LABEL`,
`BUCKET_TOOLTIP`, `bucketMatches` so the page filters correctly across the
nine canonical backend statuses + the lowercase legacy ones from SSE
replay.
`ui/app/events/page.tsx` (mine).
Tests: extended `__tests__/EventStatusBadge.test.tsx` from 3 → 13 cases.

---

## BLOCKER · 2. Events list crashes on first keystroke in search box

**Repro:**
1. `/events` is healthy, 50 rows visible.
2. Click the "Search headlines…" input, type any letter (e.g. `B`).
3. React throws `TypeError: Cannot read properties of null (reading
   'toLowerCase')` at `useMemo[items]` and the cards grid disappears.

**Root cause:** event id=82 has `headline: null` from a legacy ingestion
row, so `e.headline.toLowerCase().includes(q)` blows up the whole memo and
the entire grid unmounts.

**Why a real evaluator hits this:** *anyone* searching anything kills the
page. Console doesn't surface this to a non-DevTools user — they just see
an empty page.

**Fix:** `ui/app/events/page.tsx` — guard both `headline` and `source` with
`(value ?? "").toLowerCase()` before comparison; `EventCard.tsx` also
shows an italic `(no headline)` / `unknown source` placeholder when the
field is missing so the card row isn't blank.

---

## BLOCKER · 3. `/trigger/event` blocks the entire FastAPI event loop

**Repro:**
1. Click "Trigger live demo" on home page.
2. The POST to `/trigger/event` takes 60–75 s synchronously because the
   handler runs the whole 4-LLM + judge + Polymarket pipeline inline.
3. While that POST is in-flight, *every other API call hangs*: the events
   list polling, the SSE stream for unrelated events, even `/healthz`.
4. After 60 s the request still hasn't returned, the TriggerButton's
   `await triggerEvent()` is still suspended, and `router.push` to the new
   event id never fires.

**Why this is the user's primary complaint:** "I triggered a demo and
couldn't find it in the events page" — because the page can't actually
poll while the trigger is pending; by the time it does, the user has
already given up and reloaded.

**Status:** escalated to the backend sub-agent (B). Frontend can't fix
the root cause but I added defensive timeouts (see #4).

---

## BLOCKER · 4. Loading skeleton becomes permanent when backend is slow

**Repro:**
1. Trigger a demo, immediately go to `/events`.
2. Backend is busy serving the 60 s trigger; `GET /events` times out.
3. `useEventList` uses `useQuery` with `retry: 1` — after the retry, the
   query enters error state but the page only checks `isLoading`. The
   query has no abort signal, so each fetch waits 30 s default before
   giving up.
4. The page stays on six grey skeleton tiles indefinitely.

**Fix:**
- `ui/hooks/useEventList.ts` — added an 8 s `AbortController` timeout
  around `fetchEvents()` so failed polls fail fast, and use
  `placeholderData: (prev) => prev` so the last successful payload stays
  on-screen while the next poll is in flight.
- `ui/app/events/page.tsx` — added an explicit error banner (`role=alert`)
  with a "Retry now" button when `isError && !data`, distinguishing the
  *failed* state from the *empty* state.

---

## ANNOYING · 5. `/events/9999` (nonexistent id) showed network-error copy

**Repro:** Go to `/events/9999` while backend is healthy. API returns 404
quickly with `{"detail":"event_not_found"}`. UI showed *"Backend didn't
respond"* which is misleading because it did respond.

**Fix:** `ui/app/events/[id]/page.tsx` — sniff the error message for
"404" and show a proper "Event not found" message + back-link instead of
the network-error copy.

---

## ANNOYING · 6. Raw SCREAMING_SNAKE_CASE statuses leak into the UI

**Repro:** Any event in `EVALUATING` / `SUBMITTED` / `AUCTION_OPEN` /
`COMMITTED` etc. — the badge rendered the raw enum value because the old
EventStatusBadge map only knew about lowercase synthetic statuses.

**Fix:** EventStatusBadge now delegates to `lib/status.ts/statusInfo`,
which maps all 9 backend canonical values + the lowercase legacy ones to
friendly labels (`Auctioning`, `Judging`, `Anchored`, `Settled`, etc.).
Backend raw is preserved in `title` / `aria-label` for hover tooltip +
screen-readers.

---

## ANNOYING · 7. Featured-events strip on home shows the just-triggered event at the wrong route

**Repro:** Click "Trigger live demo" — backend creates event 102 — POST
returns (eventually) — `router.push("/events/102")` fires. But the
hovering Featured-events card I clicked *before* the trigger finished was
still routing to its own pre-trigger href, so the user can end up on a
*different* event detail page than the one they just triggered.

**Status:** mitigated by the fast-fail in #4 (the events list refreshes
in 5 s once the backend recovers and the new row jumps to the top). The
real fix is in TriggerButton's `await triggerEvent()` blocking pattern —
that's TriggerButton.tsx (D's territory).

---

## ANNOYING · 8. Loading state cannot be cleared without a hard refresh

**Repro:** After hitting Trigger and seeing `"Done — navigating to event
detail…"`, the button stays `disabled=true` because the SSE
`event.finalized` arrives but `eventId` was never set by the
still-pending POST. There's no Cancel.

**Status:** captured but not fixed — touching TriggerButton.tsx would
collide with D's `triggered` state work.

---

## COSMETIC · 9. Filter chip "all" loses pressed state after HMR

**Repro:** Save any file in the UI directory; Fast Refresh rebuilds the
events page; the `all` chip sometimes drops `aria-pressed=true` for one
frame before re-applying. Real evaluator wouldn't see this; only
relevant for dev builds.

**Status:** not fixing — production build is unaffected.

---

## COSMETIC · 10. Empty `source` field renders nothing where the dot
should be

**Repro:** Event 100, 115, 67-72 all have `source: ""`. The card used to
render `<span></span>` → tight whitespace where the source name would
sit.

**Fix:** `EventCard.tsx` now renders an italic `unknown source`
placeholder so the card layout stays balanced.

---

## COSMETIC · 11. Events list "Showing 50 of 50" was missing before fix

**Repro:** Before the events page rewrite, there was no count anywhere on
`/events`. With 50 rows and four filter buckets, evaluators couldn't
tell whether `Running (0)` meant "filter wrong" or "actually zero".

**Fix:** `ui/app/events/page.tsx` shows `SHOWING N OF M EVENTS · refreshing…`
above the grid (with the live `isFetching` indicator).

---

## Bugs I observed but didn't fix (out of file ownership)

- TriggerButton hang state has no Cancel (`components/TriggerButton.tsx`,
  D's file).
- Event-detail "Headline:" row is blank when backend returns `null`
  (`components/event/EventTimeline.tsx`, D's file).
- `app/page.tsx` home counter shows `All events (0)` while backend is
  warming up — should keep last known number or show `?`.
- Backend `/trigger/event` blocks the asyncio loop — needs to be moved to
  a background task / queue (B's file).
- Leaderboard sort doesn't re-number the rank column after re-sort
  (`components/reputation/LeaderboardTable.tsx`, D's file).

---

## Test coverage added

- `__tests__/EventStatusBadge.test.tsx` — 13 cases (was 3) covering all
  9 canonical backend statuses + lowercase legacy + null/empty fallback.
- `npx tsc --noEmit` clean across owned files (`events/page.tsx`,
  `events/[id]/page.tsx`, `EventCard.tsx`, `EventStatusBadge.tsx`,
  `useEventList.ts`).
