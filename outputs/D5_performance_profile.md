# D5 — Lifecycle Performance Profile

**Scope:** 5 mocked PASS events driven through `polyglot_alpha.orchestrator.run_lifecycle()` via the A1 harness `tests/run_pass_path_audit.py` + `tests/_pass_path_mocks.py`. All 11 judge LLM calls are short-circuited to a canned PASS verdict. Polymarket is forced to `dry_run`. Arc-testnet on-chain TXs (`openAuction`, `settle`, `commitQuestion`, two `recordFill` legs) **execute for real** on Arc testnet (free gas).

**Measurement source:** `outputs/audit_event_{39..43}.json` (`phase_timestamps` field — captured by the `_AuditSink` pubsub interceptor in `run_pass_path_audit.py`).

**Aggregate:** mean **2.58 s** / median 2.63 s / p95 2.85 s per event (5/5 reach status=SUBMITTED with full subsystem coverage including 90/10 builder-fee split). Sequential single-process throughput ≈ **23 events/min**.

---

## 1. Phase transition latency (n=5)

All values in **milliseconds**, computed from the timestamp of each pubsub topic in `phase_timestamps`.

| Transition | mean | median | p95 | min | max |
|---|---:|---:|---:|---:|---:|
| `event.created` → `auction.opened` | 14.15 | 1.75 | 104.8 | 0.48 | 63.49 |
| `auction.opened` → `auction.settled` | 9.12 | 6.11 | 35.6 | 4.14 | 23.73 |
| `auction.settled` → `translation.completed` | 8.24 | 0.91 | 58.9 | 0.60 | 35.94 |
| `translation.completed` → `quality.verdict` | 1.31 | 1.19 | 2.55 | 0.94 | 2.05 |
| `quality.verdict` → `onchain.committed` | 3.08 | 3.00 | 3.81 | 2.52 | 3.64 |
| `onchain.committed` → `polymarket.submitted` | 3.87 | 1.25 | 24.6 | 0.83 | 15.02 |
| `polymarket.submitted` → **`builder_fee.accrued`** | **2538.72** | **2497.14** | **2866.3** | **2169.8** | **2832.0** |
| `builder_fee.accrued` → `event.finalized` | 0.02 | 0.02 | 0.03 | 0.02 | 0.03 |

`builder_fee.accrued` dominates: **~98 % of total wall-clock** lives in this one transition. Translator + judge phases are effectively free because LLMs are mocked; with real Anthropic calls the panel alone will dominate (see §3).

The two early-phase p95 spikes (`event.created → auction.opened` 105 ms, `auction.settled → translation.completed` 59 ms) are first-event cold-path: SQLite WAL warmup + lazy import of `polyglot_alpha.chain.builder_fee_router`. Events 2–5 stay in the 0.5–6 ms band.

---

## 2. Top-3 hot paths by wall-clock

| # | Hot path | Latency | Source |
|---|---|---|---|
| 1 | **Builder-fee 90/10 split** — 2 sequential Arc `recordFill` TXs (winner + treasury) | **~2.5 s / event** (2.17–2.83 s) | `chain/builder_fee_router.py::record_fill_with_split` calls `record_fill` twice; each leg = `_next_nonce` (RPC: `eth_getTransactionCount`) + `gas_price` (RPC) + `send_raw_transaction` (RPC) under `send_with_nonce_lock` (`onchain.py:343-369`). No receipt wait, but 3 RPC round-trips × 2 legs ≈ 1.2 s/leg. |
| 2 | **First-event cold imports** — `polyglot_alpha.chain.builder_fee_router` is imported lazily inside `_get_chain_builder_fee_router` (`orchestrator.py:126-145`) | up to 64 ms one-shot | First-event-only spike on `event.created → auction.opened` |
| 3 | **SentenceTransformer + FAISS first hit (D8)** | **25.4 s cold** (sbert import 12.5 s + model load 7.0 s + FAISS read 0.09 s + first encode warm-up) | `corpus/lookup.py::Lookup.load` — measured on this machine in §4. Cached after first event via module-level `_DEFAULT_LOOKUP` (`corpus/lookup.py:142-153`). Not visible in 5-event audit because *all 5 events share the same already-warm process*. |

Subsystem one-shot benchmarks measured this run (uvicorn cold start, before the first PASS event):

| Subsystem | Cold | Warm |
|---|---:|---:|
| `import sentence_transformers` | 12 474 ms | ~0 ms |
| `SentenceTransformer('all-MiniLM-L6-v2')` instantiate | 7 044 ms | ~0 ms (singleton) |
| `import faiss` | 880 ms | ~0 ms |
| `faiss.read_index('corpus/polymarket_index.faiss')` (111 MB, ntotal=75 897) | 92 ms | ~0 ms |
| `Lookup.load()` end-to-end | 25 441 ms | ~0 ms (`_DEFAULT_LOOKUP` cached) |
| `find_similar(query, k=5)` (D8 lookup) — first call after load | 332 ms | 111 ms |
| Anthropic LLM round-trip | **n/a (mocked)** — code default timeout 30 s, semaphore-throttled at 5 concurrent | — |
| Arc TX submission (write) — sub+nonce+gasPrice | ~3–15 ms (commit, settle), ~1.2 s (`recordFill` includes signing+broadcast inside `send_with_nonce_lock`) | — |
| Arc TX `wait_for_transaction_receipt` (`commitQuestion` only) | ≤4 ms observed (testnet finality fast); 60 s hard cap in code (`chain/question_registry.py:170-172`) | — |
| IPFS pin | Local-file fallback (no `PINATA_JWT` / `W3S_TOKEN` / daemon at :5001 set) → write to `outputs/ipfs_pins/<sha256>.json` ≈ sub-ms. Real Pinata path would be 100–500 ms with 8 s hard timeout (`ipfs.py:48`). | — |

**Real demo extrapolation** (when judge mocks are removed): the panel fans out 8 D-judges + MQM in parallel, each capped at `PER_JUDGE_TIMEOUT_S=60`. Median Haiku 4.5 round-trip is ~1.5–3 s; the panel will dominate at **~3–8 s/event**, pushing total ≈ 5–11 s/event end-to-end. The 2.5 s builder-fee floor remains.

---

## 3. Caching opportunities

Listed in priority order — top items are biggest demo wins.

1. **Cache the two Arc `OnChainClient` web3 contracts at module scope.** `chain/builder_fee_router.py::record_fill` constructs `client = onchain or OnChainClient()` every leg (line 147) and `client.w3.eth.contract(...)` again every call. The 2 sequential legs each do a fresh RPC for `eth_gasPrice` + `eth_getTransactionCount`. Batch both legs into one `multicall`-style approval+credit, OR pre-fetch one `gas_price` and share across legs. Realistic saving: **~600–1000 ms / event**.
2. **`_load_wallet_map()` in `agents/dispatch.py:71`** is called from `resolve_agent_name` and reads `outputs/agent_wallets.json` from disk on every lookup. No `@lru_cache`. Tiny per-call cost but it sits on the hot translator-dispatch path; trivial fix with `@functools.lru_cache(maxsize=1)`.
3. **FAISS+SBert (D8) — already cached** via `_DEFAULT_LOOKUP` singleton in `corpus/lookup.py:142-153` (thread-locked). Verified: warm `_get_default_lookup()` returns in ~0 ms; `find_similar` drops from 332 ms (first) → 111 ms (subsequent). Improvement opportunity: **pre-warm at uvicorn startup** so the first ever event doesn't pay the 25 s cold tax on its D8 judge.
4. **Polymarket corpus (`corpus/polymarket_questions.parquet` + `index_meta.json` 11.6 MB)** is only loaded transitively via the FAISS path — already covered by point 3, but it appears `corpus/db_ingestion.py` reads the parquet on each invocation. Out of the lifecycle hot path, safe to leave.
5. **Judge weight table (`_WEIGHTS` in `judges/panel.py:88-105`)** is already a module-level dict — no I/O. Demo-mode `_audit_weight_access` writes a JSONL line to `outputs/weight_access_log.jsonl` per read. Best-effort, fine in non-demo; consider a debounce in demo mode if the log file grows large.
6. **Agent wallets** — `agents/wallets.py::derive_all_wallets` is HD-derived once at startup; not on the per-event hot path. Already effectively cached.
7. **Lazy `chain.builder_fee_router` import** (`orchestrator.py:126-145`) costs ~60 ms on the first event. Hoist to top-level import or pre-warm at startup.

---

## 4. Tunable env vars (performance-relevant)

| Env var | Default | Source | Effect |
|---|---|---|---|
| `ANTHROPIC_MAX_CONCURRENCY` | `5` | `llm.py:159, 224-229` | Global semaphore across all Anthropic Haiku 4.5 calls. Limits panel/critic/refine parallelism. Increase carefully — Anthropic 429s if too high. |
| `ANTHROPIC_TIMEOUT_MULTIPLIER` | `1.0` | `llm.py:176-205` | Multiplies the 60 s (base) / 120 s (loaded) per-call timeout. Useful when network is flaky. |
| `LIFECYCLE_MAX_CONCURRENCY` | `1` | `orchestrator.py:1250-1278` | How many `run_lifecycle()` invocations run in parallel. Default 1 because each parallel lifecycle keeps FAISS + SBert in RAM. Phase-2 plan (per comment): set 2–3 once module-level caching is verified. |
| `AUCTION_WINDOW_SECONDS` | `60` | `orchestrator.py:172-174` | Auction bid-collection window. Audit forces `0.0` via `auction_window_seconds=0.0` kwarg (instant settle). In production, **this single env var dominates wall-clock** if mocked bidding is off. |
| `QUALITY_PASS_THRESHOLD` | `0.7` | `orchestrator.py:176-178` | Pass/fail cutoff on aggregated quality score (0–1). Lower → fewer rejections. |
| `PANEL_TIMEOUT_SECONDS` | `120` | `orchestrator.py:656` | Outer wall-clock cap on the entire judge panel. Trip this and the event short-circuits to FAIL. |
| `PER_JUDGE_TIMEOUT_S` | `60` | `judges/panel.py:37, 234-271` | Per-judge cap so one hung LLM doesn't block all 11 judges. |
| `DEFAULT_STAKE_USDC` | `5` | `orchestrator.py:175` | Stake amount each agent posts. Affects on-chain `approve` + `submitBid` but not wall-clock directly. |
| `POLYMARKET_MODE` | `dry_run` (`polymarket/client.py:63`) | `polymarket/client.py:46-69` | `mock` skips even the dry-run HTTP shape; `real` posts to Gamma API. `dry_run` is essentially free (sub-ms). |
| `POLYGLOT_DEMO_MODE` | unset | `judges/panel.py:115-116` | Gates access to `_weights`; on the demo path adds a JSONL write to `outputs/weight_access_log.jsonl` per evaluate. |
| `_DEFAULT_PIPELINE_TIMEOUT_SECONDS` (constant, not env) | `120.0` | `agents/dispatch.py:49` | Translator-pipeline wall-clock cap. Convert to env-overridable if demo needs faster failure. |
| `DEFAULT_REFINE_TIMEOUT_S` | `45.0` | `agents/refine.py:58` | Refine-loop budget. |
| `DEFAULT_DEBATE_TIMEOUT_S` | `90.0` | `agents/internal_debate.py:48` | Internal debate budget. |
| `_MODERATOR_TIMEOUT_S` | `60.0` | `agents/moderator.py:66` | Moderator phase. |
| `_CRITIC_TIMEOUT_S` | `30.0` | `agents/critics.py:69` | Per-critic phase. |
| `DEFAULT_TIMEOUT` (LLM) | `30.0` | `llm.py:128` | Per-`complete` LLM call timeout. |
| `_HTTP_TIMEOUT_SECONDS` (IPFS) | `8.0` | `ipfs.py:48` | Per-provider IPFS HTTP timeout (Pinata, w3s, daemon). |
| `DEFAULT_HTTP_TIMEOUT` (RSS) | `15.0` | `ingestion/rss_aggregator.py:34` | Off the audit path (RSS bypassed via `user_payload`-style title). |
| `DEFAULT_TIMEOUT_SECONDS` (Polymarket) | `15.0` | `polymarket/client.py:44` | Per HTTP call to Gamma. |
| `_SQLITE_BUSY_TIMEOUT_MS` | `10000` | `persistence/db.py:32, 78` | SQLite `PRAGMA busy_timeout`. Hot if `LIFECYCLE_MAX_CONCURRENCY` is bumped. |

Other env vars present but *not* performance-tunable: `ANTHROPIC_API_KEY`, `ARC_TESTNET_RPC`, `ARC_CHAIN_ID`, `HACKATHON_WALLET_*`, `PLATFORM_TREASURY_ADDRESS`, `POLYMARKET_BUILDER_*`, `PINATA_JWT`, `W3S_TOKEN`, `REDIS_URL`, `REDIS_CHANNEL`, `CORS_ORIGINS`, `POLYGLOT_LLM_BACKEND`, `POLYGON_RPC`.

---

## 5. Verdict

**The 2.5-second builder-fee 90/10 split (two sequential Arc `recordFill` TXs in `chain/builder_fee_router.py::record_fill_with_split`) is the dominant per-event cost in the mocked audit; with live Anthropic calls re-enabled the judge panel becomes co-dominant at ~3–8 s/event, but the builder-fee floor still gates demo throughput at ~24 events/min single-process.**

The single highest-leverage demo win is parallelising the two `record_fill` legs (or batching them into a single `multicall`) — that alone cuts ~1.2 s off every PASS event with no schema changes and no contract redeploy.
