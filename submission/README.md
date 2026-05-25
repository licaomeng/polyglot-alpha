# PolyglotAlpha v2 — Submission Overview

**Hackathon:** Agora Agents (Canteen × Circle) · virtual, May 11–25, 2026
**Submission category:** Hook 04 ("Translation as alpha") · RFB 03 ("Prediction Market Verticals")
**Deadline:** 2026-05-25 23:59 ET

## Polymarket builder attribution

PolyglotAlpha is a **registered Polymarket builder**. Every market our pipeline submits routes maker fees to our builder wallet on every downstream fill — this is the revenue mechanism that funds the translator-agent auction.

| Field                  | Value                                                                |
|------------------------|----------------------------------------------------------------------|
| Builder name           | `polyglot-alpha`                                                     |
| Builder address        | `0x3d423b073a7bb0f79d2f20d65593db09aa80d8bf`                         |
| Builder code           | `0xa93402f8ae6ac4a7b1d863d80145daa74f89cb4834fc0d86b36c1e4e1d6fbeb1` |
| Maker fee rate         | 0.4% (pending → effective **2026-05-29**)                            |
| Coverage               | All markets submitted by PolyglotAlpha route fees to this builder    |

If a trader fills any market we submit (live or future), 40 bps of taker fee on that fill is routed to our builder address and from there to the winning translator wallet via `BuilderFeeRouter.sol`. See §5.15 (unit economics) and §5.30 (honest scope) in the full thesis.

## Tagline

> A multilingual translation auction for Polymarket on Arc — agents bid USDC for the right to translate non-English news into Polymarket-shaped questions; an 11-judge panel scores the output; builder fees stream back to the winning translator wallet on every fill.

## Team

| Role | Handle |
|------|--------|
| Solo builder | `licaomeng` |

Single-person submission. Field 6 on the Google form = `1 (Solo)`.

## Tech stack (one-line summary)

- **On-chain (Arc testnet, chain `5042002`):** 6 deployed Solidity contracts (`TranslationAuction`, `QuestionRegistry`, `BuilderFeeRouter`, `ReputationRegistry`, `JudgePanel`, `MockUSDC`) — all hardened with `ReentrancyGuard`. Toolchain: Foundry.
- **Off-chain orchestrator:** Python 3.14, FastAPI, async asyncio state machine, SSE for live UI updates.
- **Translator agents (4):** DeepSeek-V3, Gemini-1.5-pro, Llama-3.3-70B (via OpenRouter), Qwen-2.5 — each with its own wallet, prompt, and bid strategy.
- **11-judge panel:** 3 translation judges (BLEU-weighted MQM, COMET reference-free, LLM-MQM) + 8 style-alignment judges (D1 structural, D2 stylistic-embedding kNN, D3 framing, D4 granularity, D5 resolution clarity, D6 source reliability, D7 leading/leakage entropy, D8 duplicate FAISS kNN).
- **Corpus (+11):** 5K+ Polymarket questions scraped from gamma-api, embedded with `sentence-transformers/all-MiniLM-L6-v2`, FAISS-indexed, distilled into `style_guide.md` + `few_shots.json` + `patterns_report.md`.
- **UI:** Next.js 14 (App Router) + Tailwind + shadcn/ui + React Flow 12 (workflow DAG) + Framer Motion (7-phase timeline) + recharts + viem.
- **Settlement:** USDC-denominated bids, stakes, and fee accrual. Arc uses USDC for native gas, so the same asset is the bid, the slash, and the gas — one denomination across the whole loop.

## What was built — 10+1 components

| #   | Component              | Status                          |
|-----|------------------------|---------------------------------|
| 1   | Event Watcher          | Implemented (RSS + cross-ref)   |
| 2   | TranslationAuction.sol | Deployed (Arc testnet)          |
| 3   | Translator Agents (4)  | Implemented (mock-strategy bidders, real LLM bindings) |
| 4   | 5-Layer Pipeline       | Implemented                     |
| 5   | 11-Judge Panel         | Implemented                     |
| 6   | QuestionRegistry.sol   | Deployed (Arc testnet)          |
| 7   | Polymarket V2 Client   | Mock-first, real REST scaffolded|
| 8   | BuilderFeeRouter.sol   | Deployed (Arc testnet)          |
| 9   | ReputationRegistry.sol | Deployed (Arc testnet)          |
| 10  | UI Dashboard (7 pages) | Implemented                     |
| +11 | Polymarket Corpus      | Indexed (FAISS + few-shots)     |

Full component-to-file map in the repo `README.md` and `submission/architecture.md`.

## Deployed contracts (Arc testnet, chain `5042002`)

Addresses below reflect the latest deploy after the ReentrancyGuard hardening pass.

| Contract              | Address                                      |
|-----------------------|----------------------------------------------|
| TranslationAuction    | `0xE046Ea8478855A653bAdc9Fbd12ae4B8A429907a` |
| BuilderFeeRouter      | `0xcE7596d9b21333Eae441E912699514F6fBD150e5` |
| ReputationRegistry    | `0x00267FD2FFabDDB48bBF16e3a91C15DE260eF9F1` |
| JudgePanel            | `0x1eE7BADc48b52B36e086adb4a98E00cbff4efd9a` |
| QuestionRegistry      | `0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1` |
| MockUSDC              | `0x477fC4C3DcC87C3Ceb13adc931F6bBeDAcCa391D` |

Explorer: `https://testnet.arcscan.app`
Funded deployer / demo wallet: `0x928a7f8b37898e51E368D26869dc860DD7BF9390`

## Links

| Field                 | URL                                              |
|-----------------------|--------------------------------------------------|
| GitHub source code    | `https://github.com/licaomeng/polyglot-alpha`    |
| Live demo             | `TODO_VERCEL_URL` (Vercel deployment in progress)|
| Loom video demo (≤3 min) | `TODO_LOOM_URL`                               |

## Submission form quick-fill

| Form field                | Answer                                                                                  |
|---------------------------|-----------------------------------------------------------------------------------------|
| 1. Project Name           | PolyglotAlpha v2                                                                        |
| 2. GitHub Handle          | `licaomeng`                                                                             |
| 6. Team size              | 1 (Solo)                                                                                |
| 7. Team Members Names     | `licaomeng`                                                                             |
| 8. Problem Statement      | See `submission/qa.md` Q1                                                               |
| 9. Project Description    | See this README + `submission/architecture.md`                                          |
| 10. Traction              | 5 deployed Arc-testnet contracts; 4 multi-LLM agents bidding USDC; 11-judge attestation pipeline running end-to-end; UI deployed; corpus indexed (5K questions). See `submission/qa.md` Q4. |
| 11. Source Code           | `https://github.com/licaomeng/polyglot-alpha`                                            |
| 12. Project Live          | `TODO_VERCEL_URL`                                                                       |
| 13. Video Demo            | `TODO_LOOM_URL` (script in `submission/demo_script.md`)                                  |
| 14. Arc OSS opt-in        | ☑ Yes — MIT-licensed, reusable primitives exposed                                        |
| 15. Arc OSS narrative     | Submit API spec + reputation EWMA + builder-fee router are forkable primitives. See `submission/qa.md` Q15. |

## Cross-references in this submission folder

| File                              | Purpose                                              |
|-----------------------------------|------------------------------------------------------|
| `submission/README.md`            | this file                                            |
| `submission/demo_script.md`       | shot-by-shot 3-minute video script                   |
| `submission/qa.md`                | 20 anticipated evaluator questions + answers         |
| `submission/architecture.md`      | Mermaid component graph + phase lifecycle            |
| `submission/honesty_statement.md` | what's real, what's mocked, what's out-of-scope      |
| `submission/contact.md`           | cold-response template if Canteen reaches out        |

## Anchor citations (full thesis)

The full thesis lives in `/Users/messili/codebase/agora-agents-hackathon/README.md`. Key sections this submission depends on:

- §5.0 — Vision (translation auction market framing)
- §5.0.5 — 10-component design (full ASCII flow)
- §5.1 — Hook 04 alignment scorecard (7/7 sub-clauses)
- §5.5 — 5-Layer translation pipeline (Component 4 internals)
- §5.6 — 11-Judge panel (3 translation MQM + 8 style-alignment)
- §5.22 — 8-dimension style-alignment evaluator
- §5.23 — Ground-truth Polymarket corpus (Component +11)
- §5.27 — Information-disclosure paradox (closed evaluator IP rationale, Moody's analogy)
- §5.28 — Hayek tacit-knowledge argument
- §5.30 — Honest scope statement
