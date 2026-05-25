# Backend Monitor Report (during Playwright run)

Monitor session 2026-05-25 ~15:54Z. Backend PID 66124 on :8000, frontend on :3001.

## Snapshot 1 (t=0, ~15:54Z)

### Backend log
- 16 lines total.
- Only INFO-level access logs (GET /health, /events, /events/1, /sse/events?event_id=1). No ERROR / WARNING / Traceback / HTTP 5xx.

### Frontend log
- 19 lines. Next.js 14.2.18 compiled `/`, `/events`, `/events/[id]` successfully (HTTP 200).
- 2 warnings: `Fast Refresh had to perform a full reload due to a runtime error` (cosmetic; full reload recovered).

### DB state
```
events: 4 rows
bids: 10 rows
auctions: 4 rows
translations: 4 rows
quality_scores: 4 rows
questions: 0 rows
polymarket_submissions: 0 rows
builder_fee_events: 0 rows
agent_reputation: 5 rows
corpus_markets: 79073 rows
```
Last 3 events (all REJECTED):
- (4, REJECTED, hash 2ecdd2..., 15:43:53)
- (3, REJECTED, hash 54f73b..., 15:34:00)
- (2, REJECTED, hash 2a2e84..., 15:34:00)

Last 5 bids: agent addresses include the seeded 4 LLM agents plus one all-`b` test wallet.

### HTTP probe (all HTTP 200)
| Endpoint | Status | time | size |
| --- | --- | --- | --- |
| GET /health | 200 | 1.9 ms | 15 B |
| GET /events | 200 | 3.6 ms | 1426 B |
| GET /events/1 | 200 | 2.3 ms | 387 B |
| GET /events/1/bids | 200 | 3.6 ms | 663 B |
| GET /leaderboard | 200 | 2.1 ms | 636 B |
| GET /agents/0xllama_agent | 200 | 3.6 ms | 147 B |

### Triggered events
- Event 5 (Beijing fiscal stimulus, 4 bids): `{"event_id":5,"status":"REJECTED","verdict":"FAIL","overall_score":0.53}`
- Event 7 (BOJ rate, 2 bids — event 6 appeared in DB triggered by Playwright between calls): `{"event_id":7,"status":"REJECTED","verdict":"FAIL","overall_score":0.53}`

### SSE probe
- `GET /sse/events` (no event_id): hangs, no payload in 8 s.
- `GET /sse/events?event_id=7` returns proper SSE headers + a single `event: hello\ndata: {"subscribers": 1}` heartbeat, then idle. **No** auction/translation/quality lifecycle events broadcast.

### Status distribution
All 7 events in DB: **REJECTED**. Quality scores frozen at 0.53 (events 4-7) or 0.1 (events 1-3). 0 events ever reach PASS.

## Snapshot 2 (t=+3min, ~16:00Z)

### Backend log
- 77 lines (+61 since Pass 1). No ERROR / Traceback / 5xx.
- One torchmetrics deprecation `UserWarning: pkg_resources is deprecated`.
- Two `orchestrator: pipeline adapter not available; using mock translator` lines (the real translator is NOT being invoked — pipeline falls back to mock).
- Sentence-Transformers `all-MiniLM-L6-v2` model loaded on demand (D8 dedup judge); FAISS loaded.
- **404s from Playwright agent** (endpoints exercised but missing on the backend):
  - `GET /agents` — 404
  - `GET /events/6/phases` — 404
  - `GET /events/6/translations` — 404
  - `GET /builder_fees` — 404
- Multiple `POST /trigger/event` 200s during the interval (Playwright agent triggered events 5, 6, 7).

### Frontend log
- 37 lines (+18). Newly compiled routes: `/leaderboard`, `/agents/[address]`, `/history`, `/about`, `/events/[id]` — all HTTP 200.
- 2 additional `Fast Refresh full reload` warnings (no error stack — cosmetic).

### DB state
```
events: 7 rows (+3)
bids: 20 rows (+10)
auctions: 7 rows (+3)
translations: 7 rows (+3)
quality_scores: 7 rows (+3)
questions: 0
polymarket_submissions: 0
builder_fee_events: 0
agent_reputation: 5
```
- Status distribution: `{REJECTED: 7}` (100% rejection).
- Verdict distribution: `{FAIL: 7}` (no PASS, no BORDERLINE).
- Quality scores frozen: events 1-3 = 0.10, events 4-7 = 0.53 (suspiciously deterministic — same inputs/translation → same panel output).

### HTTP probe (all HTTP 200, no degradation)
| Endpoint | Status | time |
| --- | --- | --- |
| /health | 200 | 2.6 ms |
| /events | 200 | 11.4 ms |
| /events/1 | 200 | 9.1 ms |
| /events/1/bids | 200 | 3.6 ms |
| /leaderboard | 200 | 4.0 ms |
| /agents/0xllama_agent | 200 | 3.3 ms |

### Leaderboard correctness bug
Auctions table shows clear winners (e.g. event 1 → 0xllama_agent), but `/leaderboard` returns `total_wins: 0` for every agent and `avg_quality: 0.0`, `cumulative_fees: 0.0`. The leaderboard aggregator never reads from `auctions` (or only counts wins where the event reached PASS, which never happens).

### SSE
- `/sse/events?event_id=6` returns hello heartbeat only (263 bytes), then idle until timeout. Same behavior as Pass 1 — no lifecycle broadcasts for already-finalized events.

## Snapshot 3 (t=+6min, ~16:04Z)

### Major event: backend was reloaded
Between Pass 2 and Pass 3 **another agent edited the source code and uvicorn `--reload` restarted the backend**:
- New PID: `51893` (reloader 51822). Old PID 66124 gone. Note uvicorn now running with `--reload`, which was NOT the original launch flag.
- Backend log truncated to 16 lines (fresh process). Frontend log grew to 53 lines, mostly `✓ Compiled in ...ms` from HMR triggered by API contract changes.
- Files modified in the last 10 min (mtime check):
  - `polyglot_alpha/orchestrator.py`
  - `polyglot_alpha/api/main.py`
  - `polyglot_alpha/api/routes/events.py`
  - `polyglot_alpha/api/routes/leaderboard.py`
  - `polyglot_alpha/api/routes/agents.py`
  - `polyglot_alpha/api/routes/builder_fees.py` (NEW file)
  - `polyglot_alpha/judges/translation/comet_judge.py`

### Backend log
- 16 lines. No ERROR / Traceback. One torchmetrics `pkg_resources` deprecation warning. SentenceTransformer + Lightning checkpoint upgrade messages on first request to the dedup judge.

### Frontend log
- 53 lines (+16). Mostly `✓ Compiled in ...ms` HMR cycles as backend API shape morphed; 2 more `Fast Refresh full reload due to a runtime error` warnings (still no stack traces in the log).

### DB state
```
events: 10 rows (+3 since Pass 2)
bids: 22 rows (+2)
auctions: 8 rows (+1)
translations: 8 rows (+1)
quality_scores: 10 rows (+3)
questions: 3 rows (+3) — first time questions populated!
polymarket_submissions: 3 rows (+3) — first time populated!
builder_fee_events: 3 rows (+3) — first time populated!
agent_reputation: 5
```
Status distribution: `{REJECTED: 7, SUBMITTED: 3}` (the new events 8/9/10 reached full pipeline completion).

Verdict distribution: `{FAIL: 7, PASS: 3}`. Scores: events 1-3=0.10, events 4-7=0.53, events 8-10=0.62 (PASS).
- Note: `QUALITY_PASS_THRESHOLD` env default is 0.7 but events 8-10 PASS at 0.62. Either the env was set <=0.62 before relaunch, or the judge panel adapter was patched to override verdict.

### HTTP probe (all HTTP 200, new endpoints alive)
| Endpoint | Status | size |
| --- | --- | --- |
| /health | 200 | 15 B |
| /events | 200 | 4997 B |
| /events/8 | 200 | 4038 B |
| /events/8/bids | 200 | 343 B |
| /events/8/phases | 200 | 1960 B (NEW) |
| /events/8/translations | 200 | 774 B (NEW) |
| /leaderboard | 200 | 1083 B |
| /agents/0xllama_agent | 200 | 496 B |
| /builder_fees | 200 | 610 B (NEW) |

### Breaking schema changes since Pass 2
- `/events` now returns **bare array** instead of `{items: [...]}`. `id` field is **string** ("8") not int (8). New fields: `source`, `headline`.
- `/leaderboard` now returns **bare array**, items have new camelCase fields `address`, `alias`, `reputation`, `revenueUsd`, `winRate`; original `agent_address`/`total_wins`/`avg_quality`/`cumulative_fees` retained as aliases (good — backward-compatible).
- `/agents/{address}` now returns `address`, `alias`, `totalRevenue`, `wins`, `losses`, `winRate`, `history` array; original keys gone (breaking change).
- Leaderboard correctness: `0xqwen_agent` shows `total_wins=3, cumulative_fees=3.0, avg_quality=0.62` — fix applied. Previously stuck at all-zero.

### SSE
- `/sse/events?event_id=8` returns hello heartbeat only (263 bytes), idle until timeout. No improvement.


## Aggregated findings

- **Critical errors detected:** 0 (zero ERROR / Traceback / 5xx in backend log across all 3 passes).
- **Failed HTTP endpoints during run:** at Pass 2, 4 endpoints 404 from the Playwright agent: `/agents`, `/events/{id}/phases`, `/events/{id}/translations`, `/builder_fees`. By Pass 3, the latter 3 had been implemented (`/agents` collection endpoint is still 404).
- **Schema gaps confirmed at /events/{id}:**
  - Original schema (Pass 1-2) returned only `{id, content_hash, sources, language, title, triggered_at, status}` — no `winner_address`, no `verdict`, no `overall_score`, no auction info. Auction winners live in the `auctions` table but are not exposed.
  - By Pass 3 the schema expanded substantially (added `description`, `headline`, `source`, etc.), and `/events/{id}/phases` + `/events/{id}/translations` were added.
- **Lifecycle states observed across all events:**
  - REJECTED: 7 (events 1-7, all stuck at FAIL verdict)
  - SUBMITTED: 3 (events 8-10, full pipeline including on-chain commit + Polymarket simulation)
  - No NEW / AUCTION_OPEN / COMMITTED transitions captured in API responses or SSE (intermediate states elapse too fast or are not surfaced).
- **Events that reached PASS:** 3 / 10 total (all in Pass 3 after code changes).
- **Events that reached Polymarket submit:** 3 / 10 (all simulated, `is_simulated=true`, `market_url=mock-...`).
- **SSE event types observed:** Only `event: hello` with `data: {"subscribers": 1}` heartbeat. No `auction.opened`, `auction.settled`, `translation.completed`, `judges.evaluated`, `onchain.committed`, `polymarket.submitted`, or `builder_fee.accrued` events received — even though `orchestrator.publish(...)` calls are visible in source code at line 705-711 etc. The SSE broadcaster appears to fire-and-forget without queueing for late subscribers.

## Bugs to flag

- **[bug][HIGH]** [polyglot_alpha/api/routes/leaderboard.py + orchestrator.py:475] Before the Pass-3 patch, `total_wins` was only incremented when an event reached COMMITTED. Because every event 1-7 was REJECTED, the leaderboard reported `total_wins=0` and `avg_quality=0.0` for all agents, even though `auctions` table has clear winners. The auction-winner ≠ committed-translator distinction was not surfaced anywhere. Evidence: at Pass 2, `/leaderboard` returned `total_wins:0, avg_quality:0.0, cumulative_fees:0.0` for every agent. By Pass 3 a fix was deployed (qwen now shows 3/7 wins).
- **[bug][HIGH]** [polyglot_alpha/api/routes/events.py — schema] Pass-3 changed the `/events` envelope from `{items:[...]}` to a bare array and changed `id: int` to `id: string`. This is a backwards-incompatible API contract change shipped mid-session; any caller written against Pass-1 schema will break.
- **[bug][HIGH]** [polyglot_alpha/orchestrator.py mock-translator fallback] Backend logs the line `orchestrator: pipeline adapter not available; using mock translator` twice during Pass 2 — meaning the real translator pipeline is **not** wired and the system silently falls back to the mock for events triggered by Playwright. The mock produces titles like `"Will Will the People's Bank of China announce a cut to the Reserve Requirement Ratio (RRR) before August 23, 2026??"` with the word `Will` duplicated and a doubled `?` — a string-concatenation bug in the mock translator (probably `f"Will {title}?"` without checking the title already starts with "Will" or ends with "?").
- **[bug][MEDIUM]** [polyglot_alpha/orchestrator.py:84 + judges/panel.py:317] All 4 events triggered with different titles produced exactly the same `overall_score=0.53`, `verdict=FAIL` — suggests deterministic fallback in offline panel (mock COMET / BLEU / MQM returning constant values), not real evaluation. Defeats the purpose of having the judge panel.
- **[bug][MEDIUM]** [polyglot_alpha/api/routes/sse.py] SSE returns only the `hello` heartbeat. No lifecycle events broadcast even when an event is actively progressing through phases. UI components polling SSE for status updates will never receive them; they fall back to repeatedly polling `/events/{id}` (visible in backend log: 10+ GETs to `/events/6` within seconds).
- **[bug][LOW]** [polyglot_alpha/api/routes/agents.py — Pass-3 schema] `/agents/{address}` schema changed from `{agent_address, total_bids, total_wins, avg_quality, cumulative_fees, last_updated}` to `{address, alias, totalRevenue, wins, losses, winRate, history}` — original keys removed (truly breaking). At least the leaderboard kept aliases.
- **[bug][LOW]** [polyglot_alpha/api/main.py launch flags] Backend was restarted mid-session into `--reload` mode by an unknown actor. Reload-on-source-edit is appropriate for dev but means an external Playwright run is racing with code changes. The monitor saw the API contract morph under load.
- **[warn][LOW]** [polyglot_alpha/__init__.py imports] Two `Fast Refresh had to perform a full reload due to a runtime error` warnings on the frontend (no stack traces emitted to the log, but indicates a React state inconsistency on initial route compile).

## Recommendations

1. **Pin the orchestrator pipeline adapter:** decide whether prod runs the real translator pipeline or the mock, and fail loudly (raise) rather than silently fall back. Today's log line `orchestrator: pipeline adapter not available; using mock translator` is INFO, but for a demo this should be a startup-time check that exits non-zero if misconfigured.
2. **Fix the mock-translator title concat bug:** `f"Will {title}?"` → use a normalizer that strips leading "Will " and trailing "?" before reformatting.
3. **Surface auction winners on `/events/{id}`:** even before the judge panel runs, the UI should be able to display "0xllama_agent won the auction with bid 0.95" via the API. Expose `winner_address`, `winning_bid`, `settlement_tx_hash` from the `auctions` table on the event detail endpoint.
4. **Implement an SSE lifecycle broadcaster:** the orchestrator already calls `publish("auction.opened", ...)`, `publish("onchain.committed", ...)`, `publish("builder_fee.accrued", ...)` — wire these to the SSE `subscribers` queue keyed by event_id, and ensure subscribers receive the *current* state on connect (snapshot + delta), not just future deltas.
5. **Stabilize the public API schema:** before more Playwright runs, freeze the `/events`, `/leaderboard`, `/agents/{address}` envelopes. The Pass-3 churn (`items→array`, `id: int→string`, `agent_address→address`) will break any client written against the previous shape. Add OpenAPI versioning if breaking changes are needed.
6. **Don't run `uvicorn --reload` during Playwright UI tests:** schema changes mid-run invalidate the test plan. Use `--workers 1` without `--reload` for any test session.
7. **Real judge panel:** investigate why every offline-translated event scores exactly 0.53 — likely the COMET/BLEU/MQM mocks all return the same constant. Either seed with stochasticity or wire the real models (the SentenceTransformer + COMET checkpoint were loaded in Pass 3, so the real judges are partially available).
8. **Add `/agents` list endpoint:** Playwright tried `GET /agents` and got 404. Pair the existing `/agents/{address}` detail with a list endpoint paginated by `total_bids` or `cumulative_fees`.
