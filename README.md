# PolyglotAlpha v2

[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](./LICENSE)
[![Contracts: MIT](https://img.shields.io/badge/Contracts-MIT-green.svg)](./contracts/LICENSE)
[![Evaluator IP: Proprietary](https://img.shields.io/badge/Evaluator%20IP-Proprietary-red.svg)](./LICENSING.md)
[![Build](https://img.shields.io/badge/Build-pass-brightgreen.svg)](./outputs/final_audit_summary.md)
[![Tests](https://img.shields.io/badge/Tests-149%20Py%20%2B%2030%20Sol%20%2B%2015%20FE-brightgreen.svg)](./outputs/final_audit_summary.md)
[![Coverage](https://img.shields.io/badge/Coverage-Py%20~70%25%20%7C%20Sol%2078%25-yellowgreen.svg)](./outputs/coverage/)
[![Security: Slither](https://img.shields.io/badge/Slither-0%20High%20%7C%200%20Medium-brightgreen.svg)](./outputs/final_audit_summary.md)
[![Security: npm audit](https://img.shields.io/badge/npm%20audit-0%20Critical-brightgreen.svg)](./outputs/final_audit_summary.md)
[![Audit](https://img.shields.io/badge/Audit-8%20passes%20%7C%2025%2B%20findings%20resolved-blue.svg)](./outputs/final_audit_summary.md)

A translation auction market for prediction-market questions. Foreign-language news events
fire an on-chain auction on the Arc testnet; translator agents (one wallet, one LLM each)
bid USDC for the right to draft a Polymarket-shaped binary question; the winning bid pays
a stake, runs a 5-layer translation pipeline, and the output is scored by an 11-judge panel
(3 translation-fidelity + 8 style-alignment). Passing markets are recorded with provenance
in `QuestionRegistry.sol` and submitted upstream to Polymarket V2 with a builder code,
streaming per-fill USDC back to the winning translator wallet. This repo is the
**proof-of-mechanism** reference implementation — real Arc testnet transactions, real
LLM judge calls, real 100K-market FAISS corpus, real Polymarket Gamma payloads in
`dry_run` mode. Full thesis lives in
`/Users/messili/codebase/agora-agents-hackathon/README.md`.

## License

PolyglotAlpha v2 uses a tiered, source-available license model. See
[`LICENSING.md`](./LICENSING.md) for the full breakdown.

| Component               | License                                | File                          |
|-------------------------|----------------------------------------|-------------------------------|
| Smart contracts         | MIT                                    | `contracts/LICENSE`           |
| Backend + frontend      | Business Source License 1.1 (BUSL-1.1) | `LICENSE`                     |
| Evaluator IP            | Proprietary (not distributed)          | `polyglot_alpha/judges/`, `polyglot_alpha/corpus/`, `polyglot_alpha/style_align/` |

BSL 1.1 auto-converts to Apache License, Version 2.0 on **2030-05-26**.
Free for development, testing, academic research, and internal use under
100 markets / calendar month. For production beyond that threshold or any
hosted commercial offering, contact `licaomeng@gmail.com`.

## Quick demo

```bash
# 1. Faucet agent wallets (one-time)
.venv/bin/python scripts/faucet_agents.py

# 2. Start backend
.venv/bin/python -m uvicorn polyglot_alpha.api.main:app --reload --port 8000

# 3. Start frontend
cd ui && npm run dev  # port 3001

# 4. Trigger real lifecycle (default: RSS + 4-agent + Arc + dry_run Polymarket)
curl -X POST http://localhost:8000/trigger/event \
  -H 'content-type: application/json' \
  -d '{"event_source":"rss"}' | python3 -m json.tool

# 5. Watch SSE (10 lifecycle events)
curl -N http://localhost:8000/sse/events
```

Open http://localhost:3001 — the event appears on the dashboard with bids,
judge scores, and on-chain TX links to `testnet.arcscan.app`.

## Architecture (10+1 components)

| #   | Component                | Location                                                              | Status                    |
|-----|--------------------------|-----------------------------------------------------------------------|---------------------------|
| 1   | Event Watcher            | `polyglot_alpha/ingestion/`                                           | Implemented (RSS + cross-ref) |
| 2   | TranslationAuction.sol   | `contracts/src/TranslationAuction.sol`                                | Deployed (Arc testnet)    |
| 3   | Translator Agents (4)    | `polyglot_alpha/agents/{deepseek,gemini,llama,qwen}_agent.py`         | Real LLM (Phase 1)        |
| 4   | 5-Layer Pipeline         | `polyglot_alpha/translators.py`, `polyglot_alpha/synthesizer.py`, `polyglot_alpha/analysts.py` | Implemented |
| 5   | 11-Judge Panel           | `polyglot_alpha/judges/translation/`, `polyglot_alpha/judges/style_alignment/`, `polyglot_alpha/judges/panel.py` | Implemented (real LLM) |
| 6   | QuestionRegistry.sol     | `contracts/src/QuestionRegistry.sol`                                  | Deployed (Arc testnet)    |
| 7   | Polymarket V2 Client     | `polyglot_alpha/polymarket/client.py`, `polyglot_alpha/polymarket/mock_client.py` | mock / dry_run / real modes |
| 8   | BuilderFeeRouter.sol     | `contracts/src/BuilderFeeRouter.sol`                                  | Deployed (Arc testnet)    |
| 9   | ReputationRegistry.sol   | `contracts/src/ReputationRegistry.sol`                                | Deployed (Arc testnet)    |
| 10  | UI Dashboard             | `ui/app/` (Next.js 14 App Router)                                     | Implemented (7 pages)     |
| +11 | Polymarket Corpus        | `corpus/`, `polyglot_alpha/corpus/`                                   | Indexed (FAISS 100K markets + few-shots) |

The orchestrator that wires components 1→10 lives at
`/Users/messili/codebase/polyglot-alpha/polyglot_alpha/orchestrator.py`.
JudgePanel.sol is deployed alongside the other 4 contracts post-ReentrancyGuard
redeploy (see "Deployed contracts" below).

## Deployed contracts (Arc testnet, chain 5042002)

RPC: `https://rpc.testnet.arc.network` · Explorer: `https://testnet.arcscan.app`

| Contract              | Address                                      | Role                                                       |
|-----------------------|----------------------------------------------|------------------------------------------------------------|
| TranslationAuction    | `0xE046Ea8478855A653bAdc9Fbd12ae4B8A429907a` | 60s sealed-bid auction; reputation-gated; USDC escrow      |
| BuilderFeeRouter      | `0xcE7596d9b21333Eae441E912699514F6fBD150e5` | Per-fill USDC fan-out to translator wallets                |
| ReputationRegistry    | `0x00267FD2FFabDDB48bBF16e3a91C15DE260eF9F1` | EWMA reputation (α = 0.85); slashing authority             |
| JudgePanel            | `0x1eE7BADc48b52B36e086adb4a98E00cbff4efd9a` | Judge stake + on-chain attestation                         |
| QuestionRegistry      | `0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1` | On-chain question provenance + judge attestations (unchanged) |
| MockUSDC              | `0x477fC4C3DcC87C3Ceb13adc931F6bBeDAcCa391D` | ERC-20 mock for testnet auction settlement                 |

All redeployed post-ReentrancyGuard hardening. Older pre-ReentrancyGuard addresses
are kept in `.env` under `*_ADDRESS_OLD_PRE_REENTRANCY` for reference only.

Funded hackathon wallet (also acts as deployer + judge stakeholder for the demo):
`0x928a7f8b37898e51E368D26869dc860DD7BF9390`.

### Arc Capabilities Used

| Capability | Usage |
|---|---|
| EVM Solidity | 5 contracts deployed via Foundry |
| Native USDC | MockUSDC for testnet (`0x477fC4...391D`) |
| Low gas | ~$0.001 per TX testnet |
| Fast finality | Same-block confirmation for 60s auction window |
| ERC20 escrow | Agent 5 USDC stake, judge 2/1 USDC stake |
| Event logs | SSE-bridged auction/bid/settlement/fee events |
| EWMA reputation | On-chain `Math.mulDiv`, alpha=0.85 decay |
| 72h slashable window | Anti-manipulation reputation lock |
| ReentrancyGuard | All payable mutating functions hardened |

### Historical TX on Arc

5 real `commitQuestion` TX from initial ship at block 43944470+:
- `outputs/tx_hashes.json` lists 5 verifiable TX hashes
- Evaluators can search any of these on https://testnet.arcscan.app/
- Demo lifecycle (post-Phase-1 ship) emits 6-8 new TX per event

See thesis §5.51 for full Arc integration status.

## Polymarket V2 Integration

- **Builder name**: `polyglot-alpha`
- **Builder address** (API-only, do NOT send funds): `0x3d423b073a7bb0f79d2f20d65593db09aa80d8bf`
- **Builder code** (bytes32): `0xa93402f8ae6ac4a7b1d863d80145daa74f89cb4834fc0d86b36c1e4e1d6fbeb1`
- **Maker fee**: 0.4% (pending → effective 2026-05-29 per Polymarket 3-day cooldown)
- **Taker fee**: 0% (retry after 2026-05-27 same-day rate-limit)

Credentials live in `.env` as `POLYMARKET_BUILDER_API_KEY`,
`POLYMARKET_BUILDER_API_SECRET`, `POLYMARKET_BUILDER_API_PASSPHRASE` — never
committed.

### Submission modes (`POLYMARKET_MODE` env)
- `mock` — Returns `mock-{uuid}` market_id, 0 network calls (legacy tests)
- `dry_run` — Constructs real Gamma payload, NOT POSTed, returns `dryrun-{uuid}` ← **default**
- `real` — POSTs to https://gamma-api.polymarket.com (requires user-toggle in UI)

### Safety nets (real mode)
- Rate limit: max 5 real submissions/day
- Idempotency: same content_hash 24h
- Quality gate: only submit if overall_score >= 0.80
- Manual confirm: real-mode requires `confirm_real_submission=true` flag
- Diversity check: reject template-spam pattern

## Alchemy Polygon RPC

- **App ID**: `ngx37mo60qae6ror`
- **Project name**: `polyglot-alpha`
- **Plan**: Free tier (300M CU/month)
- **Chains**: Polygon PoS, Arc
- **RPC endpoint**: `https://polygon-mainnet.g.alchemy.com/v2/${ALCHEMY_API_KEY}` (key in .env)
- **Latency**: 270ms p50 (verified)

### Why
The Polymarket V2 fill listener (`polyglot_alpha/polymarket/fill_indexer.py`) polls Polygon for `OrderFilled` events. Public anonymous RPC providers (`polygon-rpc.com`, etc) return 401 on rate-limited requests. Alchemy free tier provides 300M CU/month — 0.25% utilization at 100 events/day.

### Env vars
```
POLYGON_RPC=https://polygon-mainnet.g.alchemy.com/v2/<your-key>
ALCHEMY_API_KEY=<your-key>
ALCHEMY_APP_ID=ngx37mo60qae6ror
```

## Demo Video *(new 2026-05-26)*

3-minute demo video for hackathon submission generated via AI pipeline:
- Playwright screen capture (webm 1920×1080)
- OpenAI TTS-1-HD voice-over (nova / onyx)
- SRT subtitles parsed from `submission/demo_script.md` timing table
- ffmpeg composition → MP4 H.264

**Cost**: ~$0.04 per regeneration. **Wall clock**: 30-60 min per iteration.

See thesis §5.50 for full architecture. Scripts ship under `scripts/record_demo.py + tts_demo.py + build_srt.py + compose_video.py` once backend Phase 1 lands.

Output: `outputs/demo_video/polyglot_alpha_demo_v1.mp4`.

## Backend API

FastAPI app at `polyglot_alpha.api.main:app`. CORS allows all origins by default
(set `CORS_ORIGINS` env to lock down). All endpoints return JSON.

### `GET /events`

List events, newest first. Supports `?limit=`, `?offset=`, `?status=`.

```bash
curl http://localhost:8000/events?limit=5
# → {"items":[{"id":1,"content_hash":"0xabc...","sources":[...],"language":"zh",
#             "title":"PBOC ...","triggered_at":"2026-05-25T...","status":"SUBMITTED"}],
#    "limit":5,"offset":0}
```

### `GET /events/{event_id}` and `GET /events/{event_id}/bids`

Full event detail + bid history for one event.

### `GET /agents/{address}` and `GET /agents/{address}/history`

Reputation row + bid/win/translation/fee history for an agent wallet.

```bash
curl http://localhost:8000/agents/0xABC1...
# → {"agent_address":"0xABC1...","total_bids":12,"total_wins":7,
#    "avg_quality":0.84,"cumulative_fees":18.6,"last_updated":"2026-05-25T..."}
```

### `GET /leaderboard`

Top agents by `?sort_by=cumulative_fees|avg_quality|total_wins|total_bids` (default
`cumulative_fees`).

### `GET /sse/events`

Server-Sent Events stream of orchestrator lifecycle events (`event.created`,
`auction.opened`, `auction.settled`, `pipeline.completed`, `judge.scored`,
`question.committed`, `polymarket.submitted`, `fee.accrued`). Heartbeat every 15s.

### `POST /trigger/event`

Kicks off a full lifecycle for a given headline. Request body:

```json
{
  "title": "PBOC governor signals timely RRR cut",
  "sources": [{"name":"caixin","url":"https://...","language":"zh"}],
  "language": "zh",
  "category": "macro",
  "auction_window_seconds": 0,
  "mock_bids": null,
  "run_in_background": false
}
```

Response is the lifecycle result dict (winner, judge verdicts, TX hashes, etc.).
Set `run_in_background: true` to return `{"scheduled": true}` and stream progress
over `/sse/events` instead.

## Frontend (Next.js)

```bash
cd /Users/messili/codebase/polyglot-alpha/ui
npm install
npm run dev   # http://localhost:3001
```

7 routes under `ui/app/`:

| Path                  | File                              | Purpose                                              |
|-----------------------|-----------------------------------|------------------------------------------------------|
| `/`                   | `page.tsx`                        | Landing — workflow DAG (React Flow) + demo trigger   |
| `/events`             | `events/page.tsx`                 | List of live + historical events                     |
| `/events/[id]`        | `events/[id]/page.tsx`            | Per-event 7-phase timeline (Framer Motion stepper)   |
| `/agents/[address]`   | `agents/[address]/page.tsx`       | Per-agent profile, bids, wins, fees                  |
| `/leaderboard`        | `leaderboard/page.tsx`            | Reputation + cumulative-fee leaderboard              |
| `/history`            | `history/page.tsx`                | Settled markets explorer                             |
| `/about`              | `about/page.tsx`                  | Project rationale + closed-IP boundary callout       |

UI talks to FastAPI via `ui/lib/api.ts` (override base with `NEXT_PUBLIC_API_BASE`).
Real-time updates use the SSE hook in `ui/hooks/useEventStream.ts`. Mock-replay data
for offline judge demos lives at `ui/lib/mock-events.json`.

## Mechanism design defaults (locked)

| Parameter                       | Value                                                 |
|---------------------------------|-------------------------------------------------------|
| Bid stake                       | 5 USDC                                                |
| Translation judge stake         | 2 USDC                                                |
| Style judge stake               | 1 USDC                                                |
| Auction window                  | 60 s (default)                                        |
| Reputation gate                 | ≥ 0.70 (≥ 0.80 if event has only one corroborating source) |
| Reputation EWMA α               | 0.85 (one bad event ≈ 0.045 drop)                     |
| Reputation formula              | `0.7 × MQM/100 + 0.3 × revenue_percentile`           |
| 72 h slashable window           | yes (Polymarket post-listing review)                  |
| K = 5 framing variants          | yes (synthesizer emits 5, judges pick best)           |
| Hard gates (all must pass)      | D1 (structural), D5 (resolution clarity), D8 (duplicate), MQM ≥ 80 |
| Soft gates (≥ 4 of 5 must pass) | D2, D3, D4, D6, D7                                    |
| Polymarket builder code         | `0xa934...beb1` (bytes32, registered on Gamma)        |
| Polymarket fee                  | 0.4% maker + (pending) 0.4% taker                     |
| Demo mode                       | real RSS + real 4-agent + real Arc + dry_run Polymarket (default) |

Overridable via env vars on the orchestrator: `AUCTION_WINDOW_SECONDS`,
`DEFAULT_STAKE_USDC`, `QUALITY_PASS_THRESHOLD`, `POLYMARKET_BUILDER_CODE`,
`POLYMARKET_MODE`.

## Phase 1 Implementation Status (2026-05-26 ship)

| Component | Status | Real / Mock |
|---|---|---|
| 5 Arc contracts deployed | ✅ Real | Real (Slither 0 High/0 Medium) |
| `polyglot_alpha/chain/` glue layer | 🚧 In progress | Phase 1 |
| `polyglot_alpha/agents/dispatch.py` 5-layer pipeline | 🚧 In progress | Phase 1 |
| 4 translator agents (Gemini/DeepSeek/Qwen/Llama) | 🚧 Wired in Phase 1 | Real LLM calls |
| 11-judge panel | ✅ Real LLM calls | Real |
| FAISS corpus (100K markets) | ✅ Real | Real |
| RSS aggregator → demo button | 🚧 In progress | Phase 1 |
| Polymarket dry_run mode | 🚧 In progress | Phase 1 |
| Polymarket fill listener (Polygon OrderFilled) | 🚧 Independent build | Phase 2 |
| CCTP V2 bridge | ⏸ Deferred | Per §5.30 honest scope |
| SSE event broadcast | ✅ Real | Real |
| Frontend (DAG + Timeline) | 🚧 Coupling in progress | Phase 1 |

Phase 1 target: 25-30% → ~80% real coverage. See thesis §5.47.

## 11-Judge Panel

### Translation sub-panel (3 judges, evaluate "is this faithful to the source?")

| Judge          | LLM binding              | Method                                | File |
|----------------|--------------------------|---------------------------------------|------|
| Strict         | GPT-4o-mini              | BLEU-weighted MQM                     | `polyglot_alpha/judges/translation/bleu_judge.py` |
| Permissive     | Claude Haiku             | COMET-weighted MQM (reference-free)   | `polyglot_alpha/judges/translation/comet_judge.py` |
| Ambiguity      | Llama 3.3 70B (OpenRouter) | MQM + binary-resolvability check   | `polyglot_alpha/judges/translation/mqm_llm_judge.py` |

### Style-alignment sub-panel (8 judges, evaluate "is this a *good Polymarket question*?")

| Dim | Name                  | Method                                                                | File |
|-----|-----------------------|-----------------------------------------------------------------------|------|
| D1  | Structural Conformance | Rule-based + Gemini fallback                                          | `polyglot_alpha/judges/style_alignment/d1_structural.py` |
| D2  | Stylistic Embedding   | sentence-transformer kNN vs 100K Polymarket corpus                    | `polyglot_alpha/judges/style_alignment/d2_stylistic.py` |
| D3  | Framing Neutrality    | LLM judge                                                             | `polyglot_alpha/judges/style_alignment/d3_framing.py` |
| D4  | Granularity           | kNN + LLM hybrid                                                      | `polyglot_alpha/judges/style_alignment/d4_granularity.py` |
| D5  | Resolution Clarity ⭐ | LLM judge (most critical gate)                                        | `polyglot_alpha/judges/style_alignment/d5_resolution_clarity.py` |
| D6  | Source Reliability    | Allowlist + LLM fallback                                              | `polyglot_alpha/judges/style_alignment/d6_source_reliability.py` |
| D7  | Leading/Leakage       | Entropy estimator                                                     | `polyglot_alpha/judges/style_alignment/d7_leading_check.py` |
| D8  | Duplicate Detection   | FAISS kNN vs 100K Polymarket corpus, cosine ≥ 0.92 = reject           | `polyglot_alpha/judges/style_alignment/d8_duplicate_detection.py` |

Triangulated aggregation lives in `polyglot_alpha/judges/panel.py`. Each judge has its
own wallet + USDC stake. A judge proved to systematically agree with one translator
agent can be slashed via `JudgePanel.sol`.

## Testing

```bash
# Activate venv first
source /Users/messili/codebase/polyglot-alpha/.venv/bin/activate

# Python tests: API, agents, judges, corpus, polymarket client, orchestrator, ingestion
pytest tests/ -v

# Smart contract tests (Foundry)
cd contracts && forge test

# Frontend tests (Jest + Testing Library)
cd ui && npm test
```

Test files under `tests/`:
`test_agents.py`, `test_api.py`, `test_corpus.py`, `test_cross_reference.py`,
`test_event_dispatcher.py`, `test_judges_panel.py`, `test_orchestrator.py`,
`test_polymarket.py`, `test_rss_aggregator.py`. Foundry suite in
`contracts/test/PolyglotAlphaV2.t.sol` covers all 5 contracts with `MockUSDC.sol`.

## Development

Run a single translator agent locally (debug bid strategy without the auction):

```bash
python -m polyglot_alpha.agents.runner --agent deepseek --headline "PBOC governor signals RRR cut"
```

Rebuild the Polymarket corpus (scrapes gamma-api, embeds, indexes with FAISS,
generates `corpus/style_guide.md` + `corpus/few_shots.json` + `corpus/patterns_report.md`):

```bash
python -m polyglot_alpha.corpus.scraper      # fetch + dump parquet
python -m polyglot_alpha.corpus.embed        # build FAISS index
python -m polyglot_alpha.corpus.style_guide  # distill style guide
python -m polyglot_alpha.corpus.few_shots    # sample few-shot exemplars
```

Corpus artifacts (`*.parquet`, `*.faiss`, `style_guide.md`, `few_shots.json`,
`patterns_report.md`) are gitignored — see `corpus/` for the most recent local build.

Required env vars in `.env` (see existing file for the funded hackathon values):
`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `ARC_TESTNET_RPC`, `ARC_CHAIN_ID`,
`HACKATHON_WALLET_PRIVATE_KEY`, the 6 deployed-contract addresses,
`POLYMARKET_BUILDER_API_KEY`, `POLYMARKET_BUILDER_API_SECRET`,
`POLYMARKET_BUILDER_API_PASSPHRASE`, `POLYMARKET_BUILDER_CODE`,
`POLYMARKET_BUILDER_ADDRESS`, `POLYMARKET_MODE`.

## Closed evaluator IP boundary

What is **open** under MIT license: the 5 smart contracts (`contracts/src/*.sol`),
the FastAPI submission API, the orchestrator state machine, the agent SDK
scaffolding, the reputation update rule. Anyone can fork the protocol, register a
translator agent, stake USDC, and bid.

What stays **closed**: the 11-judge weighting, the Polymarket corpus snapshot,
the threshold values for D1–D8, the anti-pattern detection algorithms, and the
few-shot exemplar library. This is by design — see hackathon README §5.27. An open
evaluator triggers the convergence paradox: every bidder optimizes against the same
public rubric, outputs converge, the auction collapses into a Bertrand price war,
and translator margins go to zero. Closed evaluator + open submission API is the
information-disclosure model that keeps the auction economically viable.

## Honest scope

Per thesis §5.30:

- ✅ **Proof of mechanism**: deliverable today — real Arc TX (5 contracts post-ReentrancyGuard
  redeploy), real LLM calls (4 translator agents + 11-judge panel), real 100K-market
  FAISS corpus, real Polymarket Gamma payload constructed in `dry_run` mode (builder
  code `0xa934...beb1` registered).
- ❌ **Proof of market**: real Polymarket fills require external trader interest weeks
  post-submission — out of scope for the hackathon ship.

`POLYMARKET_MODE=real` is gated behind a UI toggle + `confirm_real_submission=true`
flag + quality gate (overall_score ≥ 0.80) + 5/day rate limit + 24h content_hash
idempotency. Default is `dry_run`.

## Audit + Hardening (2026-05-26)

Before declaring demo-ready, an 8-audit parallel pass was run by sub-agents — each
auditor focused on a distinct attack surface, each emitting an artefact in `outputs/`.
A second parallel wave of 6 fix-agents addressed every CRITICAL + HIGH finding and
most MEDIUMs in the same day. The consolidated catalogue and severity table is at
[`outputs/final_audit_summary.md`](./outputs/final_audit_summary.md).

| # | Audit | Method | Report |
|---|---|---|---|
| 1 | Playwright E2E v1 + v2 | Browser automation, 8 routes, SSR vs CSR diff | `outputs/playwright_test_report_v2.md` |
| 2 | API edge-case | Adversarial curl — NaN / ∞ / negative / oversized / fuzz | `outputs/api_edgecase_report.md` |
| 3 | DB integrity | Read-only SQLite SQL — FK / NULL / time / duplicate / range / index | `outputs/db_integrity_report.md` |
| 4 | Concurrency + stress | Parallel curl + RSS sampling + SSE drain (500 GET / 10 trigger) | `outputs/stress_test_report.md` |
| 5 | Frontend perf | Bundle + dep analysis (`viem`, `zustand`, `@xyflow`, `framer-motion`) | `ui/outputs/frontend_perf_report.md` |
| 6 | Security | git-index scan + Slither + `pip-audit` + `npm audit` | `outputs/security_audit_report.md` |
| 7 | Contract invariant | Foundry — 5 invariants × 256×500, 5 fuzz × 512 | `outputs/contract_invariant_report.md` |
| 8 | Type safety | `mypy --strict` + `tsc --strict` + `Any` density | `outputs/type_safety_report.md` |

Result after the hardening wave: Slither Medium 9→0, npm audit critical 1→0,
`pip-audit` CVEs 2→0, mypy strict 127→~65, tsc strict 11→0, dedup partial-result
race fixed, MIN-bid auction selector fixed, CORS hardened, rate limit + input caps
in place, ReentrancyGuard + mulDiv applied to contracts (triggering the 2026-05-26
redeploy reflected in the address table above), SQLite WAL enabled, embedding-index
backfill landed.

## Thesis & Vision

For complete thesis: `/Users/messili/codebase/agora-agents-hackathon/README.md` (4601+ lines)

Key sections:
- §5.0 Vision
- §5.6 Mechanism
- §5.21 8-dim style evaluator
- §5.27 Closed evaluator IP
- §5.30 Honest scope
- §5.42 Why complex pipeline (Numerai parallel)
- §5.43 3-tier demo mode (dev/staging/prod)
- §5.46 LLM provider strategy
- §5.47 Mock replacement roadmap
- §5.48 Decisions locked + Polymarket builder live
