# PolyglotAlpha v2 — Performance Benchmark Report

_Measurement-only run. No source modifications. 30-min wall-clock cap applied._


## 1. Backend API response time

Target: p50 < 50ms, p95 < 200ms

| Endpoint | p50 (ms) | p95 (ms) | p99 (ms) | n | Verdict |
|---|---|---|---|---|---|
| GET /health | 1.66 | 14.62 | 22.56 | 50 | PASS |
| GET /events | 4.35 | 29.29 | 31.17 | 50 | PASS |
| GET /events/{id} | 4.59 | 10.78 | 23.66 | 50 | PASS |
| GET /events/{id}/bids | 3.19 | 17.4 | 33.56 | 50 | PASS |
| GET /agents/{addr} | 2.6 | 5.35 | 6.0 | 50 | PASS |
| GET /leaderboard | 2.66 | 8.71 | 12.8 | 50 | PASS |
| GET /builder_fees | 2.87 | 5.3 | 12.03 | 50 | PASS |

## 2. SSE event-stream latency

- Captured events while a background-triggered event ran: **14**.
- `first_event_after_post_ms` = -509.41 (negative — SSE reader connected before POST returned, so number reflects interleaving, not pure latency).
- Stream confirmed live. Per-event end-to-end latency requires a future run with paired timestamps.

## 3. End-to-end lifecycle p50/p95/p99

Target: p50 < 90s, p95 < 120s

- Planned iterations: 10. Completed (successful): **1**, timeouts at 180s client cap: **1**.
- Wall-clock budget forced early termination; only iter 0 finished within 180s.
- p50: **65.87s** (target <90s — within target).
- p95/p99: only 1 sample available; cannot compute distribution. Iter 1 exceeded 180s; true p95 unknown but **≥ 180s** (FAIL target <120s).

## 4. Backend cold start

- Killed uvicorn (PID 55511), restarted, time to first `/health` 200: **1.646s**.

## 5. Frontend cold start (Next.js 15 dev mode)

FCP/LCP via Playwright cold Chromium contexts. Dev mode JIT-compiles each route on first visit.

| URL | TTFB (ms) | FCP (ms) | Load (ms) | Status |
|---|---|---|---|---|
| / | 120 | 760 | 537 | 200 |
| /events | 47 | 92 | 381 | 200 |
| /leaderboard | 56 | 100 | 395 | 200 |
| /agents | 2015 | 2056 | 2335 | 404 |

- `/agents` returned 404 (no index page; only `/agents/[address]` exists). Its 2.0s TTFB reflects dev-mode 404 compile.
- Cold visit to unvisited `/about` measured separately: nav 506ms, TTFB 132ms, FCP 184ms.

## 6. SQLite query performance (~80K corpus_markets, 114 events)

| Query | Median (ms) |
|---|---|
| count corpus_markets | 0.01 |
| events last 50 | 0.05 |
| bids by event_id | 0.0 |
| corpus resolved limit100 | 0.23 |
| count outcome YES (full scan) | 0.23 |
| leaderboard agg | 0.06 |

All queries < 1ms median. Indexes effective.

## 7. Arc chain RPC latency

- `eth_blockNumber` × 30, all succeeded.
- p50 = **590.63ms**, p95 = **828.27ms**. (Public testnet RPC; latency dominated by network round-trip.)

## 8. LLM API latency (4 providers)

| Provider | Model | Elapsed (s) | OK |
|---|---|---|---|
| Gemini-2.0-Flash | gemini-2.0-flash | N/A | NO — Client error '429 Too Many Requests' for url 'https://genera |
| DeepSeek-V3 | deepseek/deepseek-chat | 2.078 | YES |
| Qwen-2.5-72B | qwen/qwen-2.5-72b-instruct | 0.758 | YES |
| Llama-3.3-70B | meta-llama/llama-3.3-70b-instruct | 0.839 | YES |

Note: Gemini hit 429 rate limit on free-tier key during the benchmark. Other three providers all under 2.1s for a one-sentence translation.

## 9. FAISS lookup latency

- 5 queries × 10 reps = 50 lookups, k=5.
- Median = **16.07ms** (target <100ms — PASS), p95 = 496.35ms (tail driven by first invocation of each query before any caching).

## 10. Memory + CPU during the benchmark window

- Samples: 437 over 442.7s (2s interval).
- RSS start → end: **172736 kB → 122320 kB** (delta -50416 kB).
- RSS max: 1466352 kB; p95: 1455024 kB.
- CPU mean: 1.52%, max: 127.5%.
- Total CPU consumed during window: **13.32 core-seconds**.

Note: window spanned the full benchmark (API → SQLite → RPC → FAISS → SSE → 2 lifecycle iterations), not exactly 10 lifecycle events.

---

## Summary

| Dimension | Measured | Verdict |
|---|---|---|
| 1. Backend API response | YES | PASS (all p50<5ms, p95<30ms) |
| 2. SSE event stream | PARTIAL | Live stream confirmed; per-event latency not isolated |
| 3. End-to-end lifecycle | PARTIAL | iter 0 = 65.87s PASS; iter 1 ≥180s FAIL p95 target |
| 4. Backend cold start | YES | 1.65s |
| 5. Frontend cold start | YES | Warm FCP 90-760ms; unvisited route ~184ms FCP |
| 6. SQLite queries | YES | PASS (all <1ms median) |
| 7. Arc chain RPC | YES | p50 591ms, p95 828ms (network-bound) |
| 8. LLM API latency | YES (3/4) | Gemini 429 rate-limited; DeepSeek/Qwen/Llama all < 2.1s |
| 9. FAISS lookup | YES | PASS (median 16ms) |
| 10. Memory + CPU | YES | RSS spike to 1.46 GB peak then trimmed (delta -50 MB end-to-end, no leak); 13.32 core-seconds total CPU |