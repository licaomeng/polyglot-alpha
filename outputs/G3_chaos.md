# G3 — Chaos Engineering Report
Date: 2026-05-26  ·  Budget: 40 min  ·  Mode: READ-ONLY (one harmless DB toggle, restored)
Frontend: http://localhost:3001 (3000 is a stray "Boxxo Localization" Next instance — unrelated).
Backend:  http://localhost:8000
Fresh events created during this run: 71 (G3 chaos test), 72 (rapid-fire bucket). 2 of 3 budget used.

Note: a sibling Playwright-driven agent was actively navigating the shared browser
during my window. I worked around it by (a) curl-driven SSE/REST probes,
(b) timing nav + evaluate carefully, (c) reading source for behaviour I couldn't
directly observe without contention.

## Per-test results

### Test 1 — Browser back/forward — PASS (partial UI observation)
- `/` → `/events` → `/events/70` → Back: URL restored to `/events`, h1="Events", page fully re-rendered.
- Forward: URL transitioned back to `/events/70` (verified via location.href right
  after `history.forward()`), but the shared browser was then yanked to `/about` by
  another agent before I could snapshot — interpreting as PASS based on the URL flip.
- Reload at `/events/70`: page re-rendered, h1 restored, badge "sse connected" within ~1 s.
- No spurious requests visible on back (network entries show only the original
  `GET /sse/events?event_id=70`). Next.js client cache served the back nav.

### Test 2 — Multi-tab same event — PASS
Two parallel `curl -N /sse/events?event_id=70` subscribers + `POST /trigger/event`.
Both clients received the *identical* event stream:
```
1 event: hello
1 event: event.created
1 event: auction.opened
3 event: bid.submitted
1 event: auction.settled
```
The backend hub fans out to all subscribers regardless of `event_id` query param
— see `polyglot_alpha/api/routes/sse.py:22-50`: `event_id` is in the URL but
**never read server-side**. Filtering is purely client-side in
`ui/hooks/useEventStream.ts:268`. Bandwidth-wise harmless at demo scale, worth
noting for production.

### Test 3 — Interrupted SSE / auto-reconnect — PASS, with a sharp edge
- EventSource auto-reconnect is *built into the browser*. Code in
  `useEventStream.ts:256-258` does `onError → setConnected(false)` and leaves the
  retry to the runtime. The "sse offline" badge (`page.tsx:136`) is the only
  user signal — there's **no banner, no toast, no retry button**.
- Reload always reconnects cleanly (verified on event 70).
- **Hard edge**: `/sse/events` is rate-limited to **10/min per IP**
  (`sse.py:48 @limiter.limit("10/minute")`). I ran 12 rapid handshakes:
  `200,200,200,200,429,429,429,429,429,429,429,429`. A flaky network that drops
  the connection more than 4× in a minute will lock the user out for the
  remainder of that minute with no UI hint of why (badge stays "offline").
- **No polling fallback for live state.** If SSE is wedged, phase transitions
  never arrive; only the 4-s `useEvent` poll (see Test 6) keeps the *summary*
  current. The DAG/timeline progress is SSE-only.

### Test 4 — Slow network — PARTIAL (code-inspection)
Could not inject a 3 s fetch wrap in the contested browser. From code:
- `useEvent` returns `isLoading` while the initial `GET /events/{id}` is in
  flight → page renders 3 `<Skeleton>` blocks (`page.tsx:76-83`). Clean.
- `useEventStream` defaults to `connected=false` until the `open` event fires
  → "sse offline" until first byte. Clean.
- react-query auto-cancels obsolete `useEvent` queries on unmount, so clicking
  Back during a slow load won't leak the response into the next route. Good.
- One potential UX trap: a slow `GET /events/{id}` on top of a healthy SSE
  means the user sees "sse connected" but stale REST data — no "fetching"
  spinner during the 4-s background refetch (`useEvent.ts:17-21`).

### Test 5 — Rapid-fire trigger clicks — PASS
5 parallel `POST /trigger/event` with identical body in 51 ms:
- 1st → `event_id:72, scheduled:true`
- 2nd-5th → `event_id:72, deduped:true, scheduled:false`
Backend dedup works perfectly (`api/routes/trigger.py:594`, content-hash +
5-min sliding window). UI: deduped responses still include `event_id`, so
front-end navigation to `/events/72` resolves correctly for every click.

### Test 6 — Back-pressure on idle detail page — PASS but wasteful
Idle `/events/70`, with the page just sitting there:
- **`GET /events/70` polls every 4 s = ~15 requests/min** (forever, even after
  status=SUBMITTED). See `ui/hooks/useEvent.ts:17` `refetchInterval: 4000`.
- 1 long-lived `GET /sse/events?event_id=70` connection + heartbeat every 15 s
  = ~4 sse messages/min.
- 404 events suppress the poll (`useEvent.ts:18`), but settled-and-finished
  events still poll forever — wasted work since SSE already covers all
  meaningful state changes after settlement.
- **Bandwidth budget per idle tab: ~15 REST reqs/min + 1 SSE stream.**

### Test 7 — Stale data after direct DB update — PASS
- `UPDATE events SET status='SUBMITTED' WHERE id=31` (was REJECTED).
- Immediate `GET /events/31` returned `SUBMITTED`.
- Restored to REJECTED, `GET /events/31` returned `REJECTED`.
The "FastAPI connection-pool snapshot" bug A1 mentioned is **not reproducible
here** — each request takes a fresh `Session = Depends(get_db)` and `session.get(Event,id)`
hits the DB live (`api/routes/events.py:343-352`). No staleness at REST.
- UI consequence: SSE wouldn't fire for an out-of-band DB change, so the
  4-s `useEvent` poll would surface the new status within ≤4 s — **the page
  *does* catch up**, just on the REST cadence, not in real time.

## Worst chaos failure
**SSE 429 lockout under network instability.** A user on flaky Wi-Fi who
disconnects/reconnects 5+ times in a minute (perfectly plausible on a train, in
an elevator) will be rate-limited *out* of the live stream. The badge says
"sse offline", there's no retry banner, no explanation, and the only signal
that anything is wrong is a small grey label in the corner. They have to wait
out the rate-limit window or refresh the whole tab repeatedly. The frontend has
no exponential back-off — it inherits the browser's default 3-s retry, which
ironically *accelerates* the lockout.

## Network rate (idle event detail page)
- ~15 `GET /events/{id}` per minute (4 s react-query poll, never stops on
  terminal-state events).
- 1 long-poll `GET /sse/events?event_id={id}` + ~4 heartbeats/min.
- ~0 other backend calls.

## SSE auto-reconnect
**Browser-native auto-reconnect works** on plain disconnect; "sse offline"
badge flips back to "sse connected" once the EventSource succeeds. **Breaks
when 10/min rate-limit triggers** — connection stays dead until the budget
refills, with no UI feedback distinguishing "network down" from "you are
rate-limited".

## Verdict
**Mostly resilient — fragile on the SSE recovery path.** REST/DB/dedup are
solid. The SSE layer has no client-side reconnection strategy, no offline
banner beyond a 10-char label, and the server's 10/min rate-limit will bite
real users on flaky networks; combined with the redundant 4-s REST poll the
system survives degraded-SSE, but the user has no idea live updates are gone.
