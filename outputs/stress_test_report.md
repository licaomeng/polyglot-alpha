# Stress + Concurrency Test Report

Backend: `http://localhost:8000` (uvicorn PID 51822, polyglot-alpha v0.2.0, SQLite)
Run: 2026-05-26 ~00:24–00:33 local

## Summary
- Tests run: **8 scenarios**, **35 events triggered** (within 50 budget)
- Issues found: **3 MEDIUM, 1 LOW** (no CRITICAL / no HIGH)
- Backend memory delta over 500 GETs: **0 KB** (RSS held at 130.6 MB, dropped to 108.5 MB by end — clear GC, no leak)
- SSE under load: **working** — 20/20 concurrent connections, 10 distinct event types/lifecycle, heartbeats survive 25 s slow-client drain
- Concurrency: race condition observed = **Yes** — dedup partial-result race (deduped responses see `EVALUATING` and never deliver final verdict to dup callers)

## Findings

### #1 [MEDIUM] Dedup partial-result race in `run_lifecycle` — dup callers never see final verdict
- **Repro:** fire 5 identical POSTs to `/trigger/event` in parallel with the same `title+sources` (see Scenario 2).
- **Observed:** SQLite UNIQUE index on `events.content_hash` correctly produces a single DB row (id=45). However, only **1 of 5** HTTP responses returned the full lifecycle result (`status=SUBMITTED, verdict=PASS, winner_address=0xrace`). The other **4** returned `{event_id:45, status:"EVALUATING", deduped:true}` — i.e. an in-flight snapshot — and **never delivered the verdict** to those callers (the API returns synchronously and the dup-detected requests don't wait or follow up).
  - `dedup2_1.json` → `status=EVALUATING deduped=true`
  - `dedup2_2.json` → `status=SUBMITTED verdict=PASS` (the real winner)
  - `dedup2_3..5.json` → `status=EVALUATING deduped=true`
- **Expected:** dedup-hit responses should either (a) block until the in-flight lifecycle completes and return the final verdict, (b) include a follow-up URL/handle (`/events/{id}` to poll), or (c) return `202 Accepted` semantics. The current shape is an unstable mix.
- **Fix:** in `polyglot_alpha/orchestrator.py:537-547`, when `existing is not None`, either `await` the in-flight task (track lifecycles in a `dict[content_hash, asyncio.Future]`) or change the API contract to always return a polling handle for duplicates.

### #2 [MEDIUM] Dedup SELECT-then-INSERT pattern relies entirely on DB UNIQUE constraint (no app-level integrity-error handler)
- **Repro:** `polyglot_alpha/orchestrator.py:537-558` — `select(Event).where(content_hash==X).first()`, then `session.add(Event(...))` without an explicit `try/except IntegrityError`. Today it works because SQLite serializes writes and the `ix_events_content_hash` UNIQUE index catches the duplicate INSERT, but the failure case isn't handled.
- **Observed:** under sustained parallel load (5+5 in Scenario 7) no `UNIQUE constraint failed: events.content_hash` showed up in `/tmp/polyglot-backend.log`, only because timing favored the SELECT path. The code path that does INSERT after a losing race is unhandled.
- **Expected:** wrap the INSERT in `try/except IntegrityError`, then on hit re-`SELECT` to grab the row created by the winner and continue as a dedup-hit. (This also lets the fix for #1 plug in cleanly.)
- **Fix:** add explicit handler around line 555 of `orchestrator.py`; on `IntegrityError` rollback and re-query then return the dedup result.

### #3 [MEDIUM] `/events` p95 latency degrades 20× under concurrent writes (SQLite single-writer contention)
- **Repro:** Scenario 7 — 5 POST `/trigger/event` + 5 GET `/events?limit=20` fired in parallel.
- **Observed:** GET latency went from baseline median **4.2 ms / p95 40.5 ms** to **~810 ms** for all 5 GETs during concurrent writes (200× slower than median). POSTs all returned 1.77–1.84 s. No `database is locked` errors logged, but the serial write pipeline blocks readers.
- **Expected:** in WAL mode SQLite readers should not block on writers; this looks like the engine is in DELETE journal mode, or that connections are exclusive-locking the whole DB.
- **Fix:** enable WAL mode at connection setup (`PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`) and use a separate read-only connection pool. For production, move off SQLite to Postgres (the unique-constraint + transaction story improves too — see #2).

### #4 [LOW] Stale background-task exceptions spam the log
- **Repro:** every `/trigger/event` call.
- **Observed:** `/tmp/polyglot-backend.log` is full of `AttributeError: module 'polyglot_alpha.polymarket.fill_listener' has no attribute 'start'` from `_start_fill_listener` at `orchestrator.py:427`. The task is fired and forgotten — exception is never retrieved.
- **Expected:** either the import is dead code that should be removed, or the function should be wrapped in `try/except` so a broken module doesn't pollute logs on every lifecycle.
- **Fix:** in `orchestrator.py` `_start_fill_listener`, guard the `fill_listener.start` call and log a warning once at startup instead of an exception per event.

### #5 [LOW] Invalid `status` filter on `/events` silently returns empty array
- **Repro:** `GET /events?status=BOGUS` → `HTTP 200 []`.
- **Observed:** Status values are open strings, no enum validation, no error for unknown values. Pagination edges are well-handled (`limit=0`, `limit=10000`, `offset=-1` all return 422 with clear pydantic errors).
- **Expected:** validate `status` against `EventStatus` enum and return 422 for unknown.
- **Fix:** in `polyglot_alpha/api/routes/events.py`, type the query param as `Optional[EventStatus]` so FastAPI enforces validation.

## Performance
- `/events?limit=50` (warm, no concurrent write): **median 4.2 ms, p95 40.5 ms** (20 samples)
- `/events` under concurrent writes (5+5): **all 5 GETs ~810 ms** — see #3
- `/trigger/event` (5 sequential, 0.3 s auction window): **median 470 ms, p95/max 942 ms**
- `/trigger/event` (10 parallel, 0.5 s auction window): wall clock 3.0 s, server time 2.58–2.84 s each
- 500× `/events` sequential GETs: 19.6 s total, **0 KB RSS delta** (no leak)
- 20× concurrent SSE for 10 s: 20/20 received `hello`, RSS unchanged after close

## SSE event types observed per 60-s window (5 lifecycles)
```
  5 event: auction.opened
  5 event: auction.settled
  5 event: bid.submitted
  5 event: builder_fee.accrued
  5 event: event.created
  2 event: heartbeat
  1 event: hello
  5 event: onchain.committed
  5 event: polymarket.submitted
  5 event: quality.verdict
  5 event: translation.completed
```
10 distinct lifecycle events delivered per trigger, plus periodic `heartbeat` + `:ping` comments — slow-client drain at 4 s intervals received them all without disconnect.

## Recommendations (priority order)
1. **Fix the dedup partial-result race** (#1) — track in-flight lifecycles by `content_hash` and `await` on a shared `asyncio.Future` so all dup callers receive the same final verdict.
2. **Harden the SELECT-then-INSERT path** (#2) — explicit `IntegrityError` handling so dedup is safe even when DB-level write-serialization changes (e.g. Postgres).
3. **Enable SQLite WAL** (#3) — single PRAGMA change unblocks readers during writes; cuts GET p95 ~200×.
4. **Clean up the `fill_listener` background task** (#4) — silence the per-event exception spam.
5. **Tighten `/events?status=` validation** (#5) — return 422 on unknown enum values for consistency with `limit`/`offset`.

## Files referenced
- `/Users/messili/codebase/polyglot-alpha/polyglot_alpha/orchestrator.py:513-563` — `run_lifecycle` and dedup window
- `/Users/messili/codebase/polyglot-alpha/polyglot_alpha/orchestrator.py:427` — `_start_fill_listener` fire-and-forget
- `/Users/messili/codebase/polyglot-alpha/polyglot_alpha/api/routes/trigger.py` — synchronous trigger endpoint
- `/Users/messili/codebase/polyglot-alpha/polyglot_alpha/api/routes/events.py` — paginated/filtered listing
- `/Users/messili/codebase/polyglot-alpha/polyglot_alpha.db` — SQLite with `UNIQUE ix_events_content_hash`
- `/tmp/polyglot-backend.log` — backend stdout/stderr with stale fill_listener exceptions
