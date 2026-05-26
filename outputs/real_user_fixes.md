# Real-User Fixes — what I shipped inline

Working dir `/Users/messili/codebase/polyglot-alpha`. Six files touched.
All pure-UI, no backend changes.

---

## 1. `ui/hooks/useEventList.ts`

**Symptom:** events list stuck on skeleton when backend is slow.
**Fix:**
- Wrap `fetchEvents` in an 8 s `AbortController` so the polled GET fails
  fast instead of hanging on the default 30 s socket timeout.
- Add `placeholderData: (prev) => prev` so the previous successful
  payload stays on-screen while the next poll is in flight — no more
  flicker back to the skeleton on every 5 s refetch.

## 2. `ui/app/events/page.tsx`

**Symptom:** filter chips silently dropped every row; search crashed
on null headlines; no error state when backend was unreachable.
**Fix:**
- Replace the local lowercase whitelist with the new `lib/status.ts`
  taxonomy (`STATUS_BUCKETS` + `bucketMatches`) so every backend
  canonical status (`PENDING`, `AUCTION_OPEN`, `AUCTION_SETTLED`,
  `TRANSLATING`, `EVALUATING`, `REJECTED`, `COMMITTED`, `SUBMITTED`,
  `FAILED`) is routed into the right bucket.
- Guard the search predicate with `(e.headline ?? "")` and
  `(e.source ?? "")` so null DB rows can't throw `Cannot read
  properties of null`.
- Render the bucket tooltip on each chip (`title={BUCKET_TOOLTIP[b]}`)
  so evaluators know what each bucket contains.
- New error banner (`role=alert`, dismissable, with a Retry button)
  surfaces `isError && !data` instead of an infinite skeleton.
- New count line — `Showing N of M events · refreshing…` — gives
  evaluators confidence the list is alive.
- Empty-state copy now distinguishes "filter selects 0" from "0 events
  total".

## 3. `ui/app/events/[id]/page.tsx`

**Symptom:** `/events/9999` etc. showed "Backend didn't respond" even
when the backend answered 404 in 3 ms.
**Fix:**
- Pull `error` out of the `useEvent` hook return.
- Sniff `error.message.includes("404")` and show "Event not found · No
  event with id … exists" instead of the network-error copy.
- Wrap the empty state in a back-link to `/events` so the user isn't
  stuck.

## 4. `ui/components/event/EventCard.tsx`

**Symptom:** cards rendered nothing where a null headline / empty
source field should sit, leaving a gap.
**Fix:**
- `(no headline)` italic placeholder when `event.headline` is falsy.
- `unknown source` italic placeholder when `event.source` is empty.

## 5. `ui/components/event/EventStatusBadge.tsx`

**Symptom:** raw `EVALUATING` / `AUCTION_OPEN` / `COMMITTED` leaked
into the UI badge.
**Fix (initially mine, then refined by sub-agent D):**
- Added all 9 backend canonical statuses to the label map with
  evaluator-friendly labels (`Auctioning`, `Settled bid`,
  `Translating`, `Judging`, `Anchored`, `Settled`, etc.).
- Sub-agent D later refactored the map into `lib/status.ts` and made
  the badge delegate to `statusInfo`. The canonical raw value is now
  preserved in `title` (hover tooltip) and `aria-label` (screen
  readers).

## 6. `ui/__tests__/EventStatusBadge.test.tsx`

Extended from 3 → 13 cases:
- Cover all 9 canonical backend enum values.
- Verify the null/empty fallback renders `Unknown` rather than an
  empty pill.
- Lock down the labels so a future taxonomy change is visible in CI.

---

## Test gates

- `npx jest __tests__/EventStatusBadge.test.tsx` → **13/13 pass**.
- `npx tsc --noEmit` clean on the 6 files I touched (errors in
  `app/history/page.tsx` are pre-existing D-domain work).

## Files touched (mine)

```
ui/hooks/useEventList.ts
ui/app/events/page.tsx
ui/app/events/[id]/page.tsx
ui/components/event/EventCard.tsx
ui/components/event/EventStatusBadge.tsx     (overlapping w/ D)
ui/__tests__/EventStatusBadge.test.tsx
```

## Files I deliberately *didn't* touch (other sub-agents own them)

- `ui/components/TriggerButton.tsx` — D / A
- `ui/components/event/EventTimeline.tsx` — D (already in diff)
- `ui/components/polymarket/BuilderCodeBadge.tsx` — D
- `ui/app/page.tsx` — A
- `ui/app/history/page.tsx`, `ui/app/leaderboard/page.tsx` — D
- All backend Python (B's domain).
