# API Edge-Case + Error Injection Report

Backend: PolyglotAlpha v2 at `http://localhost:8000`
Date: 2026-05-26
Test surface: `POST /trigger/event`, `GET /events`, `GET /events/{id}`, `GET /agents/{address}`, `GET /sse/events`

## Summary
- Total adversarial tests run: **27** (across 6 categories)
- Bugs found: **9** (Critical: 0 / High: 3 / Medium: 4 / Low: 2)
- Most concerning finding: `bid_amount: "NaN"` causes an uncaught **HTTP 500** — the only crash observed. Combined with the fact that **mock_bids bypasses Pydantic field validation** (no `ge=0`, no NaN/Inf guard, no agent-address sanity), an external caller can write arbitrary bogus data into the bids table (negative, infinite, empty-address winners).
- Dedup under concurrent load works correctly (5 parallel triggers with identical content_hash → 1 event created + 4 deduped responses, DB has exactly 1 row).
- SSE stream is healthy: ~99 events emitted across 9 event types during a single triggered lifecycle.
- `/events` performance is excellent (mean 4.6 ms, p99 23.8 ms over 100 sequential reqs).

## Findings

### #1 [HIGH] `bid_amount: "NaN"` returns HTTP 500 — uncaught exception
- **Repro:**
  ```bash
  curl -X POST http://localhost:8000/trigger/event -H 'content-type: application/json' \
    -d '{"title":"nan bid","sources":[{"name":"x","url":"https://x.com"}],
         "mock_bids":[{"agent_address":"0x1","bid_amount":"NaN"}]}'
  ```
- **Expected:** HTTP 422 with a validation error like "bid_amount must be a finite non-negative number".
- **Actual:** `Internal Server Error` (HTTP 500). `float("NaN")` succeeds inside `_coerce_bids`, then the orchestrator (or downstream math / DB write) throws.
- **Suggested fix:** Define `mock_bids` as `list[BidRecord]` (existing schema has `Field(..., ge=0.0)`), or in `_coerce_bids` guard with `math.isfinite(v) and v >= 0` and raise `HTTPException(status_code=422, detail=...)` on violation.
- **Source:** `polyglot_alpha/api/routes/trigger.py:33-63`

### #2 [HIGH] Negative / Infinite / >1.0 `bid_amount` silently accepted
- **Repro:**
  ```bash
  # All return HTTP 200 with a real event_id
  curl ... -d '{"...","mock_bids":[{"agent_address":"0x1","bid_amount":-100}]}'
  curl ... -d '{"...","mock_bids":[{"agent_address":"0x1","bid_amount":1e308}]}'
  curl ... -d '{"...","mock_bids":[{"agent_address":"0x1","bid_amount":99999.99}]}'
  ```
- **Expected:** HTTP 422 — `BidRecord.bid_amount_usdc: float = Field(..., ge=0.0)` already exists in `schemas.py:77`, plus an `le` bound for sanity (bids should be small positive USDC).
- **Actual:** Negative bids are persisted; auction may pick a negative winner. `_coerce_bids` constructs `BidRecord` with raw `float(...)` and never validates.
- **Suggested fix:** Validate via Pydantic on `TriggerRequest` (define `class TriggerBid(BaseModel): agent_address: str = Field(..., min_length=1); bid_amount: float = Field(..., ge=0.0, le=1_000_000.0)` and replace `list[dict[str, Any]]`).
- **Source:** `polyglot_alpha/api/routes/trigger.py:49-63`

### #3 [HIGH] CORS reflects arbitrary `Origin` with `allow_credentials=True`
- **Repro:**
  ```bash
  curl -X OPTIONS http://localhost:8000/trigger/event \
    -H 'origin: http://evil.com' -H 'access-control-request-method: POST' -i
  # → access-control-allow-origin: http://evil.com
  # → access-control-allow-credentials: true
  ```
- **Expected:** Either no `Access-Control-Allow-Credentials: true` (when origins is `*`) or a fixed allow-list of trusted origins.
- **Actual:** `CORS_ORIGINS` env defaults to `"*"`. Combined with `allow_credentials=True`, Starlette echoes the request origin into the response — letting any third-party site initiate **credentialed** XHR/fetch against this API from a victim's browser.
- **Suggested fix:** In `_build_cors_origins`, refuse to combine `"*"` with `allow_credentials=True` (either drop credentials or replace `"*"` with `allow_origin_regex=None` + an explicit dev allow-list).
- **Source:** `polyglot_alpha/api/main.py:33-52`

### #4 [MEDIUM] Empty `agent_address` accepted as auction winner
- **Repro:**
  ```bash
  curl -X POST http://localhost:8000/trigger/event -H 'content-type: application/json' \
    -d '{"title":"empty agent","sources":[{"name":"x","url":"https://x.com"}],
         "mock_bids":[{"agent_address":"","bid_amount":0.5}]}'
  # → {"event_id":43,...,"winner_address":"",...}
  ```
- **Expected:** HTTP 422 — `agent_address` should be `min_length>=1` (better: hex-prefix validation `^0x[a-fA-F0-9]+$`).
- **Actual:** Persisted with `winner_address=""`. This will break downstream agent-history queries and leaderboards.
- **Suggested fix:** Add `agent_address: str = Field(..., min_length=1, pattern=r"^0x[a-fA-F0-9]{1,64}$")` on the trigger Pydantic model.

### #5 [MEDIUM] Negative `auction_window_seconds` accepted
- **Repro:** `{"title":"neg window", ..., "auction_window_seconds": -5}` → HTTP 200.
- **Expected:** HTTP 422 (`ge=0.0`).
- **Actual:** Silently coerced; orchestrator likely skips the wait. Confusing API contract.
- **Suggested fix:** `auction_window_seconds: float | None = Field(default=0.0, ge=0.0, le=3600.0)`.

### #6 [MEDIUM] Invalid `?status=` query value returns 200 + empty list, not 422
- **Repro:** `curl http://localhost:8000/events?status=NOTAREAL` → `[]` (HTTP 200).
- **Expected:** HTTP 422 with allowed-values error.
- **Actual:** Misleading — a typo in a dashboard filter looks like "no results" instead of a client bug.
- **Suggested fix:** Type the `status` query param as a `Literal[...]` or `Enum`.
- **Source:** `polyglot_alpha/api/routes/events.py` (list events handler)

### #7 [MEDIUM] Source `url` accepts arbitrary non-URL strings
- **Repro:** `{"sources":[{"name":"x","url":"not-a-url"}], ...}` → HTTP 200.
- **Expected:** HTTP 422 (validate as `pydantic.HttpUrl` or `AnyUrl`).
- **Actual:** Persisted as-is. Will break ingestion if/when fetched.
- **Suggested fix:** `class TriggerSource(BaseModel): url: HttpUrl`.

### #8 [LOW] Title 10,000 chars accepted (no `max_length`)
- **Repro:** title with 10,000 'A' chars → HTTP 200.
- **Expected:** HTTP 422 with a reasonable upper bound (e.g., 512 chars).
- **Actual:** Persisted; bloats DB and SSE payloads.
- **Suggested fix:** `title: str = Field(..., min_length=3, max_length=512)`.

### #9 [LOW] SQL-injection / XSS strings in `title` stored verbatim
- **Repro:** title=`test' OR 1=1; DROP TABLE events; --` → HTTP 200; title=`<script>alert(1)</script>` → HTTP 200.
- **Expected:** Stored safely (SQLAlchemy parameterised queries — confirmed no injection actually happened). However, no XSS escaping on read paths.
- **Actual:** No backend exploit (SQLAlchemy is safe), but raw HTML/JS is returned via `/events` JSON. If any UI renders `title` with `dangerouslySetInnerHTML` (or similar), it's vulnerable.
- **Suggested fix:** Document that UI must HTML-escape user-supplied fields; optionally strip control characters server-side.

## Performance
- `GET /events` (100 sequential requests, single-threaded loop):
  - mean: **4.63 ms**
  - p50: 3.62 ms
  - p95: 10.55 ms
  - p99: **23.77 ms**
  - max: 23.77 ms
- SSE `/sse/events`: holds connection open, emits ~99 events for a single triggered lifecycle (9 distinct event types × ~11 events).
- 5 concurrent identical triggers: dedup is correct — 1 DB row, 4 "deduped:true" responses, no race observed.

## Correctness — verified passes
- Missing `title` → 422 with clear loc/msg.
- Title `min_length=3` enforced.
- `?limit=999999` → 422 (`le=500`), `?limit=-1` → 422, `?limit=0` → 422, `?offset=-1` → 422.
- `GET /events/abc` → 422; `GET /events/99999` → 404; `GET /events/-1` → 404.
- Wrong content-type → 422 (with informative body); invalid JSON → 422.
- Type-mismatched fields (`title: 12345`) → 422.
- `sources: null` → 422.

## Recommendations (priority order)
1. **Replace `mock_bids: list[dict[str, Any]]` with a typed Pydantic model.** Eliminates findings #1, #2, #4 in one change.
2. **Tighten CORS** — never combine `"*"` with `allow_credentials=True`. Either drop credentials or default to a localhost dev allow-list.
3. **Add bounds + enum validation** on `?status`, `auction_window_seconds`, `sources[].url`, `title.max_length`.
4. **Catch + map 500s in the trigger handler** — wrap `run_lifecycle` and convert math/value errors to HTTP 422 so the API never leaks unhandled exceptions during a demo.
