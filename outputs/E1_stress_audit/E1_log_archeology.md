# E1 Log Archeology — 20 PASS Events + 6 Logmine Events

**Run:** 2026-05-26, 20 PASS-path events through `run_lifecycle` with mocked panel + LLM.
**Status:** 20/20 SUBMITTED. Event IDs 45-64. Outputs in `outputs/E1_stress_audit/`.
**Also captured:** 6 additional events (IDs 65-70) at INFO log level into `E1_orchestrator_info.log` for pattern mining (in-process orchestrator log, separate from running backend's log file).

## 1. Wall-clock stats (per-event, seconds)

| stat   | seconds |
|--------|---------|
| min    | 2.20    |
| median | 2.48    |
| p95    | 2.67    |
| max    | 2.67    |
| mean   | 2.47    |

**No outliers > 5s.** Variance is tight (~0.5s spread). The median is consistent with A1's 2.85s baseline.

## 2. Per-phase wall-clock decomposition (median seconds since `event.created`)

| Phase | Median delta | p95   |
|-------|--------------|-------|
| event.created          | 0.0000 | 0.0000 |
| auction.opened         | 0.0007 | 0.0050 |
| bid.submitted          | 0.0039 | 0.0107 |
| auction.settled        | 0.0049 | 0.0124 |
| translation.completed  | 0.0058 | 0.0157 |
| quality.verdict        | 0.0077 | 0.0170 |
| onchain.committed      | 0.0097 | 0.0217 |
| polymarket.submitted   | 0.0104 | 0.0306 |
| **builder_fee.accrued** | **2.4755** | **2.6735** |
| event.finalized        | 2.4755 | 2.6735 |

**Builder-fee phase consumes ~99% of wall-clock.** Fine-grained breakdown via INFO log timestamps (6 logmine events):

| Sub-phase                            | n | min  | median | max  |
|--------------------------------------|---|------|--------|------|
| panel.evaluate → polymarket submit   | 6 | 0.002 | 0.003 | 0.013 |
| polymarket submit → recordFill(0.9)  | 6 | 0.929 | 1.116 | 1.431 |
| recordFill(0.9) → recordFill(0.1)    | 6 | 0.971 | 1.251 | 1.487 |
| panel.evaluate → record_fill_with_split done | 6 | 2.235 | 2.393 | 2.636 |

Each `recordFill` leg takes ~1.0-1.5s. They run serially (`record_fill_with_split` awaits each in turn). Parallelising the two legs could cut wall-clock by ~50% (1.2s instead of 2.4s).

## 3. SSE event counts + ordering

| stat        | count |
|-------------|-------|
| min events  | 10    |
| median      | 12    |
| max         | 14    |
| mean        | 11.8  |

Four distinct topic orderings observed — they differ ONLY by the number of `bid.submitted` events (matches V1=3, V2=3, V3=1, V4=2, V5=5). Core sequence is identical across all 20 runs:

```
event.created → auction.opened → bid.submitted{N} → auction.settled
  → translation.completed → quality.verdict → onchain.committed
  → polymarket.submitted → builder_fee.accrued → event.finalized
```

**No subsystem failed to fire on any event.** All 20 events emitted: `auction.opened`, `auction.settled`, `translation.completed`, `quality.verdict`, `onchain.committed`, `polymarket.submitted`, `builder_fee.accrued`, `event.finalized`.

## 4. Top log patterns (INFO level, 61 lines across 6 events)

| Count | Pattern |
|-------|---------|
| 6     | `polyglot_alpha.orchestrator: orchestrator: invoking panel.evaluate (title='...', timeout=120s)` |
| 6     | `polyglot_alpha.polymarket.client: polymarket dry_run submission: market_id=...` |
| 6     | `polyglot_alpha.polymarket.fill_indexer: PolygonFillIndexer ready (rpc=...)` |
| 6     | `polyglot_alpha.polymarket.fill_listener: FillListener starting market=... interval=30s` |
| 6     | `httpx: HTTP Request: POST https://polygon-mainnet.g.alchemy.com/v2/... "HTTP/1.1 200 OK"` |
| 6     | `polyglot_alpha.chain.builder_fee_router: recordFill(market=..., amount=0.9000, ...) tx=0x...` |
| 6     | `polyglot_alpha.chain.builder_fee_router: recordFill leg ... amount=0.9 usdc tx=0x...` |
| 6     | `polyglot_alpha.chain.builder_fee_router: recordFill(market=..., amount=0.1000, ...) tx=0x...` |
| 6     | `polyglot_alpha.chain.builder_fee_router: recordFill leg ... amount=0.09999999999999998 usdc tx=0x...` |
| 6     | `polyglot_alpha.chain.builder_fee_router: record_fill_with_split(...) winner_tx=0x... treasury_tx=0x...` |

## 5. Patterns NOT found (greps that returned zero hits)

* `WARNING` — zero
* `ERROR` — zero
* `Traceback` — zero
* `AttributeError`, `KeyError` — zero
* `timed out` / `timeout` (other than the static `timeout=120s` literal in panel.evaluate logs)
* `retry` — zero
* `fallback` — zero
* `skipping` — zero

The happy-path is silent at WARNING+. No exceptions surface in 20 PASS events.

## 6. DB integrity sweep (20 events)

For every event, verified:

* exactly 1 `events` row, 1 `auctions` row, 1 `translations` row, 1 `quality_scores` row, 1 `questions` row, 1 `polymarket_submissions` row, 2 `builder_fee_events` rows
* `bids` row count matches the variation spec (1, 2, 3, or 5)
* `fee_total = winner_fee + treasury_fee` is exactly `1.0` in all 20 events (the float drift on the treasury leg cancels arithmetically — see findings)
* winner from `_settle_auction` rule `min(bid/max(rep,1.0)) over qualified` matches `auctions.winner_address` in **20/20** events
* `agent_reputation.total_wins >= 1` and `cumulative_fees > 0` for winner in **20/20** events

## 7. LLM cost incurred

* `panel.evaluate` calls (mocked): 20
* `mock_llm_calls` (any non-panel path that tried LLM): 0
* `outputs/llm_cost_log.jsonl` grew by 3 lines (background process unrelated to this run; provider=`injected` so still no real Anthropic spend)

**Real Anthropic API calls during E1 run: 0.** Mocks held perfectly.

## 8. Backend log not touched

The running uvicorn process (PID 2753, port 8000) writes to its own log file we did not locate; `/tmp/polyglot-backend.log` and `/tmp/polyglot_backend.log` were both unchanged (91 and 81 lines pre/post). The audit harness runs in-process — sharing only the SQLite file. Verified post-run via `GET http://127.0.0.1:8000/events/45` → returned `status=SUBMITTED`. Backend is healthy and observed our new rows.
