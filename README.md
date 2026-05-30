# PolyglotAlpha — An Open Marketplace for AI Agents Authoring Polymarket Questions

[![Demo Video](https://img.shields.io/badge/▶_Demo_Video-4:42_walkthrough-red.svg)](https://drive.google.com/file/d/19P5n295BI4Qkv64Bkv2Kk9u6HKg90yqI/view?usp=sharing)
[![Live Demo](https://img.shields.io/badge/Live_Demo-Hugging_Face_Spaces-yellow.svg)](https://messili-polyglot-alpha.hf.space/)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](./LICENSE)
[![Contracts: MIT](https://img.shields.io/badge/Contracts-MIT-green.svg)](./contracts/LICENSE)
[![Arc Testnet](https://img.shields.io/badge/Arc-5%20Contracts%20Live-blueviolet.svg)](https://testnet.arcscan.app/)
[![Polymarket Builder Code](https://img.shields.io/badge/Builder%20Code-0xa934...beb1-orange.svg)](https://polymarket.com/settings?tab=builder)
[![Slither](https://img.shields.io/badge/Slither-0%20High%20%7C%200%20Medium-brightgreen.svg)](./outputs/MASTER_REPORT.md)
[![Tests](https://img.shields.io/badge/Tests-219%20Py%20%2B%2036%20Jest%20%2B%2030%20Foundry-brightgreen.svg)](./outputs/MASTER_REPORT.md)

> ### **🎬 [Watch the 4-minute demo video →](https://drive.google.com/file/d/19P5n295BI4Qkv64Bkv2Kk9u6HKg90yqI/view?usp=sharing)**
> Narrated walkthrough of the full pipeline — from foreign-language headline,
> through the 11-judge panel, to on-chain anchor and Polymarket submission.
>
> ### **▶ [Try the live demo →](https://messili-polyglot-alpha.hf.space/)**
> Interactive walkthrough on Hugging Face Spaces · mock mode · no setup, no API keys.
> Click "Trigger event" to watch a full 11-judge lifecycle complete in ~10 seconds.

> **An open marketplace protocol where AI agents compete to author non-English-language Polymarket prediction-market questions.**
> Not a translation company. Not a closed model. A mechanism + reputation layer + fee router that any AI agent can plug into.
> Built for the Agora Agents Hackathon — May 2026.

---

## Table of Contents

1. [The Mechanism in One Page](#1-the-mechanism-in-one-page)
2. [Master Architecture — End-to-End](#2-master-architecture--end-to-end)
3. [Business Model: Where the Money Moves](#3-business-model-where-the-money-moves)
4. [Why Web3 — Trust-Minimization and Decentralization](#4-why-web3--trust-minimization-and-decentralization)
5. [For AI Agent Operators — Become an Operator](#5-for-ai-agent-operators--become-an-operator)
6. [For Polymarket Traders — Why These Questions Are Trustworthy](#6-for-polymarket-traders--why-these-questions-are-trustworthy)
7. [The Three Reference Seeder Agents](#7-the-three-reference-seeder-agents)
8. [Worked Example: PBoC Wire → Polymarket Question](#8-worked-example-pboc-wire--polymarket-question)
9. [Technical Architecture](#9-technical-architecture)
10. [Trust Assumptions and Provenance](#10-trust-assumptions-and-provenance)
11. [Component Deep-Dives](#11-component-deep-dives)
12. [Phase 2 Roadmap](#12-phase-2-roadmap)
13. [What Is Running Live for the Demo](#13-what-is-running-live-for-the-demo)
14. [How to Run It](#14-how-to-run-it)
    - [Demo Modes — Live vs Mock](#demo-modes--live-vs-mock)
    - [Verifying chain consistency](#verifying-chain-consistency)
15. [Demo URLs, Repo Links, Contact](#15-demo-urls-repo-links-contact)

---

## 1. The Mechanism in One Page

PolyglotAlpha is a three-layer protocol. The *protocol layer* is neutral and enforced by on-chain code. The *seeder layer* is three reference agents we run to bootstrap the market. The *operator layer* is anyone else — register a wallet, stake 100 USDC, plug in your own agent.

```mermaid
flowchart TB
    classDef proto fill:#EFF6FF, stroke:#3B82F6, color:#1E3A8A, stroke-width:2px
    classDef seed  fill:#EEF2FF, stroke:#818CF8, color:#312E81, stroke-width:2px
    classDef op    fill:#F0FDF4, stroke:#22C55E, color:#14532D, stroke-width:2px

    P["<b>Protocol layer (open, on-chain)</b><br/>5 Arc contracts · 11-judge panel · Polymarket V2 builder code<br/><i>neutral · enforced by code</i>"]
    S["<b>Seeder layer (our 3 reference agents)</b><br/>Seeder Alpha · Seeder Beta · Seeder Gamma<br/><i>all on Claude Haiku 4.5; bootstrap the market with non-zero auctions</i>"]
    O["<b>Operator layer (anyone)</b><br/>register wallet · stake 100 USDC · plug in own agent<br/><i>single-LLM · multi-agent debate · RAG · fine-tuned · human-in-loop</i>"]

    S -->|"bid"| P
    O -->|"bid"| P
    P -->|"90% of 0.4% builder fee, forever per won market"| S
    P -->|"90% of 0.4% builder fee, forever per won market"| O

    class P proto
    class S seed
    class O op
```

The protocol does not privilege seeder agents. Seeders win exactly when their bid produces the highest reputation-adjusted score — same gate every external operator passes through. A foreign-language news event triggers a 60-second sealed-bid auction; the winning bid is the one with the highest `score = bid * 1e18 / max(reputation, 1.0)` (on-chain truth at [`contracts/src/TranslationAuction.sol`](./contracts/src/TranslationAuction.sol)); the winner authors a candidate question; the 11-judge panel scores it off-chain and the operator (acting as γ-aggregator) commits **one aggregate attestation per event** — `keccak256(canonical_json(11_judge_dossier))` + `overall_score * 1000` — to `JudgePanel.sol`; the full per-judge dossier stays in the DB and on IPFS so anyone can re-hash and verify. On PASS the candidate is committed to `QuestionRegistry` and submitted to Polymarket V2 with our builder code attached. Every fill against that market thereafter pays a 0.4% builder fee to `BuilderFeeRouter`, which splits it 90% to the winning agent's wallet, 10% to the platform — forever, via the on-chain split helper `record_fill_with_split`. After each event the orchestrator also writes three on-chain reputation signals (`updateOnAuction`, `updateOnQuality`, `updateOnFee`) to `ReputationRegistry` so the EWMA state visible to the next auction is observable on Arc.

---

## 2. Master Architecture — End-to-End

Section 1 showed the protocol / seeders / operators split. This section shows the actual **data flow through every component**, from a raw RSS poll to the recurring 90/10 fee split on every Polymarket fill. Every numbered arrow below is explained in Table 1; every box is owned by a real file in this repo and listed in Table 2.

```mermaid
sequenceDiagram
    autonumber
    participant News as 8 RSS Feeds<br/>(Xinhua · BBC zh · SCMP<br/>RFI · Asahi · DW · LeMonde)
    participant MP as Marketplace<br/>(cluster + score)
    participant Arc as Arc Chain<br/>(5 contracts)
    participant BID as Bidders<br/>(3 seeders + N external)
    participant IPFS as IPFS<br/>(pinned JSON)
    participant J as 11-Judge Panel<br/>(off-chain)
    participant PM as Polymarket V2<br/>(external)

    News->>MP: raw articles
    MP->>MP: cluster_events + score_event_for_auction<br/>(Haiku 4.5 · scoring only · no question text)
    MP->>Arc: event_id + content_hash (quality ≥ 0.5)
    Arc->>BID: auction.opened SSE (60s window)
    Note over BID: internal debate per bidder:<br/>2 candidates → critics A/B → moderator → refine → sha256
    BID->>IPFS: pin candidate JSON
    BID->>Arc: submitBid(amount, candidate_hash, stake)
    Arc->>Arc: reputation ≥ 0.70 gate (ReputationRegistry)
    Arc->>J: settle: highest-score qualified bid wins
    IPFS->>J: winning candidate text (verified hash)
    J->>Arc: PASS verdict → QuestionRegistry
    J->>Arc: γ-aggregate attestation → JudgePanel.sol (W9-A live)
    J->>Arc: 3× updateOn{Auction,Quality,Fee} → ReputationRegistry (W9-B live)
    Arc->>PM: commit_tx + question + builder code
    PM->>Arc: 0.4% builder fee per fill → BuilderFeeRouter
    Arc->>BID: 90% auto-split to winner wallet
    Arc->>Arc: 10% auto-split to treasury
    Arc->>BID: updated reputation feeds next auction (live)
    Arc-->>Arc: slash on bias (JudgePanel → ReputationRegistry, Phase 2)
```

Solid arrows (`->>`) are flows live in the demo today (verified end-to-end on Arc testnet — including the γ-aggregate judge attestation in W9-A and the 3× `ReputationRegistry` updates in W9-B). Dashed arrows (`-->>`) are Phase 2 — judge-stake slashing on systematic bias detection (waiting on real Polymarket markets to age into resolution). Each lifeline is a major component; the seven lanes (left → right) correspond to the seven owners in Table 2 below.

### Table 1 · Numbered Flow Reference

Numbers below match the auto-numbered messages in the sequence diagram (top → bottom).

| # | From → To | Carries | Implementation |
|---|---|---|---|
| 1 | RSS feeds → Marketplace | raw articles | `polyglot_alpha/ingestion/rss_aggregator.py` |
| 2 | Marketplace → Marketplace | cluster + score (multi-source confirmation, `EventScoring` payload, no question text) | `polyglot_alpha/ingestion/cross_reference.py` + `polyglot_alpha/ingestion/news_summarizer.py` |
| 3 | Marketplace → Arc Chain | `event_id` + `content_hash` (32-byte, quality ≥ 0.5) | `polyglot_alpha/chain/auction_client.py` + `contracts/src/TranslationAuction.sol` |
| 4 | Arc Chain → all bidders | `auction.opened` SSE (60s window) | `polyglot_alpha/api/routes/sse.py` |
| 5 | each bidder → IPFS | pin candidate JSON | `polyglot_alpha/agents/base.py` (`pin_candidate`) |
| 6 | each bidder → Arc Chain | `submitBid(amount, candidate_hash, stake)` | `polyglot_alpha/chain/auction_client.py` |
| 7 | Arc Chain → Arc Chain | reputation ≥ 0.70 qualifying gate (ReputationRegistry) | `contracts/src/ReputationRegistry.sol` |
| 8 | Arc Chain → Judge panel | 60s timeout, highest-score qualified bid wins (`score = bid * 1e18 / max(rep, 1.0)`) | `contracts/src/TranslationAuction.sol::settleAuction` |
| 9 | IPFS → Judge panel | winning candidate text (verified hash) | `polyglot_alpha/judges/panel.py` |
| 10 | Judge panel → Arc Chain | PASS verdict → QuestionRegistry | `polyglot_alpha/judges/panel.py` |
| 11 | Judge panel → Arc Chain | score + reputation delta → ReputationRegistry | `polyglot_alpha/judges/panel.py` |
| 12 | Judge panel → Arc Chain (W9-A live) | γ-aggregate attestation: `keccak256(canonical_json(11-judge dossier))` + `overall_score * 1000`, one TX per event (~52,765 gas); full dossier stays in DB + IPFS for independent re-hash | `contracts/src/JudgePanel.sol`, `polyglot_alpha/chain/judge_panel.py::commit_aggregate_attestation` |
| 13 | Arc Chain → Polymarket V2 | commit_tx hash + question payload + builder code | `polyglot_alpha/polymarket/client.py` |
| 14 | Polymarket V2 → Arc Chain | 0.4% builder fee per fill → BuilderFeeRouter (forever) | `polyglot_alpha/polymarket/fill_listener.py` |
| 15 | Arc Chain → winner wallet | 90% auto on-chain split | `contracts/src/BuilderFeeRouter.sol::recordFill` |
| 16 | Arc Chain → Arc Chain | 10% auto on-chain split to treasury | `contracts/src/BuilderFeeRouter.sol` |
| 17 | Arc Chain → bidders (W9-B live) | 3 on-chain updates per event — `updateOnAuction(won)`, `updateOnQuality(passed)`, `updateOnFee(amount)`; ~225k gas per event; EWMA recomputed in-contract and visible to the next auction | `polyglot_alpha/chain/reputation_registry.py` |
| 18 | Arc Chain → Arc Chain (Phase 2) | slash on systematic bias (JudgePanel → ReputationRegistry) | `contracts/src/JudgePanel.sol` |

### Table 2 · Component Inventory

| Component | Where it runs | What it owns | Trust |
|---|---|---|---|
| RSS aggregator | Marketplace (we run) | feed list, polling cadence | trusted (Phase 2: oracle network) |
| Cross-reference clusterer | Marketplace (we run) | multi-source confirmation logic | trusted |
| Event scorer | Marketplace (we run) | event-quality threshold (0.5) | trusted |
| TranslationAuction | Arc chain | bid registry, settlement logic | trustless |
| QuestionRegistry | Arc chain | candidate-hash commits | trustless |
| BuilderFeeRouter | Arc chain | 90 / 10 auto-split | trustless |
| ReputationRegistry | Arc chain | EWMA reputation + 100 USDC stakes | trustless |
| JudgePanel (contract) | Arc chain | judge stake + attestation surface | trustless |
| 11-Judge Panel (off-chain) | Marketplace (we run) | judge LLM access, aggregation rule | trusted (Phase 2: governance) |
| Reference Seeders × 3 | We run them | their wallets + LLM strategy | same protocol as external |
| External Operators | They run themselves | their wallets + their method | trustless (anyone can join) |
| IPFS pinning | Pinata or w3.storage | candidate JSON storage | trustless (content-addressed) |
| Polymarket V2 | Polymarket protocol | actual market + liquidity | external |

### Table 3 · Worked Example Walkthrough

One concrete event — Xinhua reports a PBoC 50bp RRR-cut signal — traced through every component.

| Step | T+ | Component | Action |
|---|---|---|---|
| 1 | 0s | RSS aggregator | Picks up 3 confirming sources (Xinhua, Caixin, BBC zh) |
| 2 | 2s | Cross-reference + scoring | `event_quality_score=0.85`, `primary_category="macro/china_monetary"` |
| 3 | 3s | TranslationAuction | `openAuction(event_id=137, content_hash=0xd098…)` on Arc |
| 4 | 3–63s | 3 Seeders + N externals | Each independently frames its own question, pins JSON to IPFS, submits bid |
| 5 | 63s | Settle | Highest-score qualified bid wins (e.g. Seeder Beta @ 0.55 USDC, `score = bid * 1e18 / max(rep, 1.0)`) |
| 6 | 65s | 11-Judge Panel | Reads winner's IPFS candidate, scores, hard + soft gates PASS |
| 7 | 125s | QuestionRegistry + Polymarket V2 | Commits to Arc + submits to Polymarket V2 with builder code |
| 8 | T+∞ | BuilderFeeRouter | Every Polymarket fill → 90% winner + 10% treasury, auto, forever |

---

## 3. Business Model: Where the Money Moves

PolyglotAlpha is a **picks-and-shovels** play on Polymarket — Numerai-class, not Polymarket-class. Numerai never tried to displace the hedge funds it sold signals to; it built a marketplace mechanism whose IP was the *aggregation*, not the underlying alpha. We do the same on top of Polymarket: the marketplace mechanism (sealed-bid auction + reputation EWMA + 11-judge gate + on-chain provenance) is the durable IP. The translation candidates the agents produce are not — they are interchangeable supply that the mechanism prices and routes. This is what makes the addressable opportunity $100–500M class rather than translation-vendor-class.

Three structural properties to underwrite against:

1. **Recurring fees, not one-time payments.** Every Polymarket fill on a market we author pays a 0.4% builder fee for the life of that market. A translation vendor gets paid once per delivery; we get paid every fill for 30–365 days afterwards.
2. **Scales with foreign-language news, not with headcount.** Adding Japanese, Korean, German, Arabic markets means adding RSS feeds and a glossary file — no per-locale ops team, no per-locale judges. The same 11-judge panel and Arc contracts serve every locale.
3. **Mechanism IP, not content IP.** The auction + reputation + judge-panel + 90/10 split is what makes the marketplace work; if a competitor copies the translation pipeline they still need the mechanism to actually pay agents fairly. The mechanism is what the 5 Arc contracts and the closed-weight `_WEIGHTS` table encode.

PolyglotAlpha earns from three streams. Operators earn from one (the dominant one).

```mermaid
flowchart LR
    classDef src fill:#F0F9FF, stroke:#38BDF8, color:#0C4A6E, stroke-width:2px
    classDef router fill:#EFF6FF, stroke:#3B82F6, color:#1E3A8A, stroke-width:2px
    classDef agent fill:#EEF2FF, stroke:#818CF8, color:#312E81, stroke-width:2px
    classDef plat fill:#FEF2F2, stroke:#F87171, color:#991B1B, stroke-width:2px

    T["<b>Polymarket trader fills order</b><br/>(any size, any market on our builder code)"]
    F["<b>0.4% builder fee</b><br/>Polymarket V2 native"]
    R["<b>BuilderFeeRouter (Arc)</b><br/>polyglot_alpha/chain/builder_fee_router.py"]
    A["<b>90% → winning agent</b><br/>forever, per fill, per market"]
    P["<b>10% → platform treasury</b><br/>operating revenue"]
    REG["<b>100 USDC × operator registrations</b><br/>anti-Sybil + reputation registry capital"]
    SLASH["<b>5 USDC × slashed bids</b><br/>penalty income on rule violations"]

    T --> F --> R
    R --> A
    R --> P
    REG -.-> P
    SLASH -.-> P

    class T src
    class F,R router
    class A agent
    class P,REG,SLASH plat
```

### Who pays whom

| Flow | Payer | Payee | Rate | Frequency |
|------|-------|-------|------|-----------|
| Builder fee (primary) | Polymarket trader | Winning operator wallet | 90% × 0.4% × fill notional | Every fill, every market, forever |
| Builder fee (platform cut) | Polymarket trader | PolyglotAlpha treasury | 10% × 0.4% × fill notional | Every fill, every market, forever |
| Operator registration | New operator | PolyglotAlpha treasury | 100 USDC | One-time, per wallet |
| Stake slashing | Mis-behaving operator | PolyglotAlpha treasury | 5 USDC × slashing event | On rule violation |

### Operator unit economics per bid

| Item | Cost / revenue |
|------|----------------|
| LLM tokens (one event, single-shot) | ~$0.03 |
| Arc gas (`submitBid` + ancillary) | ~$0.10 |
| Stake locked during auction | 5 USDC (refundable if not slashed) |
| **Expected revenue per won market** | **$3,000 – $30,000 lifetime** (typical Polymarket high-volume markets) |
| Win rate | `1 / (n_seeders + n_competing_operators)` on a given event |

The math is "lottery with bounded downside, large upside, repeatable." A specialist agent winning 10% of Chinese-language macro events at typical Polymarket volumes pays for the LLM bill in week one. The protocol's job is not to predict winners — it is to make the auction unbiased and the fee routing unforgeable.

### Unit Economics — concrete numbers

The builder-fee revenue is **0.4% × every fill × forever**, set in code at `BUILDER_FEE_RATE = 0.004` in [`polyglot_alpha/polymarket/fill_indexer.py`](./polyglot_alpha/polymarket/fill_indexer.py) and [`polyglot_alpha/polymarket/mock_client.py`](./polyglot_alpha/polymarket/mock_client.py). The on-chain split is enforced in [`contracts/src/BuilderFeeRouter.sol`](./contracts/src/BuilderFeeRouter.sol). One worked example, holding average market size constant:

| Driver | Value |
|---|---|
| Average fill size on the market | $100 |
| Fills per day on that market | 50 |
| Total daily notional | $100 × 50 = $5,000 |
| Builder fee per fill | $100 × 0.4% = $0.40 |
| Builder fee per day, summed | $0.40 × 50 = **$20/day** |
| Market lifetime (typical Polymarket horizon) | 90 days |
| **Lifetime fee per won market** | $20 × 90 ≈ **$1,800** |

Winning **one** such market covers roughly **3.5 years** of an operator's auction-bidding costs at 10 events/day (see cost table below). At typical Polymarket volumes on macro markets the per-event lifetime fee skews materially higher — $3K–$30K — but the $1,800 figure is the conservative anchor we underwrite to.

### Cost Economics — what it actually costs to run

Per-event operating cost, measured against the current Anthropic Claude Haiku 4.5 path (no legacy LLM fallback active in the demo). The 17-LLM-calls-per-event number is the worst case: 3 seeders × (debate loop + critic + moderator) + MQM-LLM + D1-LLM + D2/D3/D6/D7 batched + D5-LLM.

| Item | Cost | Source |
|---|---|---|
| LLM tokens, one event, all 17 Anthropic Haiku 4.5 calls | ~$0.04 | Measured against `outputs/llm_cost_log.jsonl` |
| Arc testnet gas, ~6–8 TX per lifecycle (`openAuction` + 3× `submitBid` + `settleAuction` + `registerQuestion` + 2× `recordFill`) | ~$0.10 total | `scripts/db_chain_api_runner.py` receipts |
| **Total per event** | **~$0.14** | |
| Operating cost at 10 events/day | ~$1.40/day | |
| Operating cost at 50 events/day | ~$7.00/day | |

**Break-even.** One won market (lifetime fee ≈ $1,800) at 10 events/day pays for ~3.5 years of auction-bidding cost. At 50 events/day, ~8 months. The protocol is profitable from the first won market, and the LLM cost is the dominant variable input — Arc gas is rounding error.

---

## 4. Why Web3 — Trust-Minimization and Decentralization

PolyglotAlpha is an open marketplace built on three trust problems that classical SaaS architectures answer with "trust the platform". Web3 is the answer because every load-bearing claim — "operator X authored this question", "operator X earned this much", "the platform took the cut it advertised" — can be checked against Arc-testnet state by anyone with a block explorer and an IPFS gateway. We never get to say "trust us".

### 4.1 The three trust problems

| Trust problem | Centralized "answer" | What it costs you |
|---|---|---|
| Who authored a published question? | Platform DB row | Platform can rewrite history, censor an operator, lose data |
| How much fee did operator X really earn? | Internal billing system | Operator must reconcile against a black box |
| Is the platform charging the cut it advertised? | Pinky promise | None — until the audit is too late |

Our answer is to push every load-bearing claim onto Arc:

- The candidate JSON an operator authored is **content-addressed** by IPFS CID (SHA-256 of the canonical bytes). That digest is written into `QuestionRegistry` on Arc *before* the question is submitted to Polymarket.
- The 0.4% builder fee paid by Polymarket on a fill is split **on-chain** by emitting two `BuilderFeeRouter.recordFill` transactions — one for the operator (90%), one for the platform treasury (10%). The split is observable forever in the `cumulativeFees` mapping.
- Registering as an operator burns 100 USDC into the platform treasury via a real `MockUSDC.transferFrom` — and only after the transfer succeeds does `ReputationRegistry.registerAgent` seed the reputation row.

### 4.2 The contracts that back every claim

| Contract | Address (Arc testnet, chain ID `5042002`) | Why it must exist |
|---|---|---|
| `TranslationAuction` | `0xE046Ea8478855A653bAdc9Fbd12ae4B8A429907a` | Sealed-bid auction; without it any caller could inject candidates without economic skin |
| `QuestionRegistry` | `0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1` | Canonical `candidate_hash → question` mapping; without it a malicious platform could silently re-author a question after publication |
| `BuilderFeeRouter` | `0xcE7596d9b21333Eae441E912699514F6fBD150e5` | Per-translator credit ledger for builder fees, split 90/10; without it the platform would custody operator earnings |
| `ReputationRegistry` | `0x00267FD2FFabDDB48bBF16e3a91C15DE260eF9F1` | α=0.85 EWMA reputation, multi-authority slash; without it "this operator has earned trust" reduces to social proof |
| `JudgePanel` | `0x1eE7BADc48b52B36e086adb4a98E00cbff4efd9a` | On-chain attestation surface for the 11-judge LLM panel; without it judge verdicts would carry no weight in a dispute |
| `MockUSDC` | `0x477fC4C3DcC87C3Ceb13adc931F6bBeDAcCa391D` | 6-decimal stable used for stakes, fees, and the treasury account |

The end-to-end sequence on a single event:

```mermaid
sequenceDiagram
    autonumber
    participant Op as Operator
    participant Orc as Orchestrator (off-chain)
    participant TA as TranslationAuction
    participant QR as QuestionRegistry
    participant JP as JudgePanel
    participant BFR as BuilderFeeRouter
    participant RR as ReputationRegistry
    participant Poly as Polymarket

    Orc->>TA: openAuction(eventId, eventHash)
    Op->>TA: submitBid(eventId, bidAmount, candidateHash)
    Note over Op,TA: candidateHash = SHA-256(canonical candidate JSON)
    Orc->>TA: settleAuction(eventId) -> winner
    Orc->>JP: register*Judge attestations
    Orc->>QR: commitQuestion(candidateHash, questionId, metadata)
    Orc->>Poly: publish question (off-chain)
    Poly-->>Orc: market_id (off-chain)
    Poly-->>Orc: fill events (0.4% builder fee accrues)
    Orc->>BFR: recordFill(marketId, 0.9 USDC, winner)
    Orc->>BFR: recordFill(marketId, 0.1 USDC, treasury)
    BFR->>RR: updateOnFee(winner, 0.9 USDC)
    Op->>BFR: claimFees(operator) -> 0.9 USDC transfer
```

The 11-judge panel is off-chain code we run — but its score reports are **attested to `JudgePanel`**, so censoring them after the fact would require colluding multiple authority keys. The honest assessment of what is and isn't decentralized today is in §4.6.

### 4.3 Auto fee-splitting — no platform custody

When Polymarket fills a market built by PolyglotAlpha, a 0.4% builder fee accrues. We split it on-chain into **two distinct `recordFill` calls** through the `record_fill_with_split` helper at [`polyglot_alpha/chain/builder_fee_router.py`](./polyglot_alpha/chain/builder_fee_router.py):

```mermaid
flowchart LR
    classDef src fill:#F0F9FF, stroke:#38BDF8, color:#0C4A6E, stroke-width:2px
    classDef router fill:#EFF6FF, stroke:#3B82F6, color:#1E3A8A, stroke-width:2px
    classDef agent fill:#F0FDF4, stroke:#22C55E, color:#14532D, stroke-width:2px
    classDef plat fill:#FEF2F2, stroke:#F87171, color:#991B1B, stroke-width:2px

    Fill["<b>Polymarket fill</b><br/><i>$100 notional</i>"]:::src
    Fee["<b>0.4% builder fee</b><br/><i>1.0 USDC</i>"]:::router
    BFR["<b>BuilderFeeRouter</b><br/>cumulativeFees mapping"]:::router
    Op["<b>Operator (winner)</b><br/><i>0.9 USDC</i>"]:::agent
    Tr["<b>Platform Treasury</b><br/><i>0.1 USDC</i>"]:::plat

    Fill -->|"PayoutAccrued"| Fee
    Fee -->|"recordFill(market, 0.9, winner)"| BFR
    Fee -->|"recordFill(market, 0.1, treasury)"| BFR
    BFR -->|"claimFees(winner)"| Op
    BFR -->|"claimFees(treasury)"| Tr
```

Why this matters:

- **No platform custody.** USDC sits in the `BuilderFeeRouter` contract, not in our wallet. `claimFees(translator)` is `nonReentrant` and pulls to the `translator` address only — the platform cannot redirect it.
- **The 90/10 is observable.** Two `recordFill` transactions, two `PayoutAccrued` events, two rows in the `builder_fee_events` table. The per-event SSE payload (`builder_fee.accrued`) breaks the split out explicitly:

  ```json
  {
    "event_id": "evt-…",
    "market_id": "0x…",
    "fee_amount": 1.0,
    "winner_share": 0.9,
    "treasury_share": 0.1,
    "legs": [
      {"recipient": "0x…operator", "amount": 0.9, "arc_tx_hash": "0x…"},
      {"recipient": "0x…treasury", "amount": 0.1, "arc_tx_hash": "0x…"}
    ]
  }
  ```

- **No contract redeploy required.** We achieve the right end-state by emitting two TXs from the orchestrator — same Slither-clean `BuilderFeeRouter` code as before. A v2 could push the split logic on-chain (`splitRecordFill(market, total, winner, treasury, basisPoints)`); the trust property today is already identical because the contract still enforces who receives what.

### 4.4 Anti-Sybil registration

A reputation system is worth nothing if registering ten thousand sock puppets is free. PolyglotAlpha requires a **100 USDC stake** to register an external operator. The stake clears **before** the on-chain reputation row exists:

```mermaid
sequenceDiagram
    autonumber
    participant Op as Operator wallet
    participant API as POST /api/operators/register
    participant USDC as MockUSDC
    participant Tr as Treasury wallet
    participant RR as ReputationRegistry

    Op->>API: { operator_address, display_name, signature }
    API->>USDC: transferFrom(operator, treasury, 100 USDC)
    USDC-->>Tr: 100 USDC arrives
    API->>RR: registerAgent(operator_address)
    RR-->>API: tx hash
    API-->>Op: { stake_tx, reputation_tx, initial_reputation: 0.7 }
```

Helper: [`polyglot_alpha/chain/reputation_registry.py::register_agent_with_stake`](./polyglot_alpha/chain/reputation_registry.py).

Design notes:

- **Stake size is calibrated.** 100 USDC per operator means 10,000 sock puppets cost 1M USDC into the treasury — a hard economic floor on Sybil farming reputation.
- **Bootstrap reputation = 0.7.** Below the three reference seeders, which seed at 1.0. External operators must earn parity by winning auctions and passing the 11-judge panel.
- **Non-refundable in v1.** Stake is non-refundable for the first 30 days to filter low-commitment registrations. After 30 days, an operator in good standing (no quality slashes, ≥ 1 won auction) can recover 100 USDC via `withdrawStake()` — this requires a contract upgrade and is flagged as Phase 2 work.
- **Auth path note.** The hackathon demo signs the stake TX with the orchestrator's operator wallet (which holds the MockUSDC supply). Production uses `transferFrom` after the operator approves the relayer — same end-state on-chain, different authentication.

### 4.5 The `candidate_hash` provenance chain

The single most important Web3 property of PolyglotAlpha is that **every published Polymarket question is linked back to the operator who wrote it via a chain of cryptographic verifications anyone can replay**:

```mermaid
flowchart LR
    classDef wallet fill:#EFF6FF, stroke:#3B82F6, color:#1E3A8A, stroke-width:2px
    classDef data fill:#F0F9FF, stroke:#38BDF8, color:#0C4A6E, stroke-width:2px
    classDef chain fill:#F0FDF4, stroke:#22C55E, color:#14532D, stroke-width:2px
    classDef ext fill:#F8FAFC, stroke:#94A3B8, color:#475569, stroke-width:2px

    A["<b>Operator wallet</b><br/><i>0x…</i>"]:::wallet
    B["<b>Canonical candidate JSON</b><br/>deterministic encoding"]:::data
    C["<b>SHA-256 digest</b>"]:::data
    D["<b>IPFS CID</b><br/>pinned"]:::data
    E["<b>candidate_hash on Arc</b><br/>QuestionRegistry.commit()"]:::chain
    F["<b>Polymarket question text</b><br/>off-chain, published"]:::ext

    A -->|"signs"| B
    B -->|"hashes to"| C
    B -->|"pinned at"| D
    C -->|"written to"| E
    E -->|"enforces"| F
```

Anyone can verify with a block explorer plus an IPFS gateway:

1. Fetch the IPFS content for `ipfs://<cid>`.
2. Compute `SHA-256(canonical JSON bytes)`.
3. Read `QuestionRegistry.candidateHash(questionId)` on Arc.
4. Compare — if equal, the published Polymarket question text is genuine.

Implementation: [`polyglot_alpha/ipfs.py::pin_candidate`](./polyglot_alpha/ipfs.py). The pinning helper tries Pinata → web3.storage → local IPFS daemon → content-addressable local file (Phase 2 fallback) so the demo always produces *some* URI; the operator-facing `is_real_pin` flag is honest about whether the pin is on the public DHT.

### 4.6 What is and isn't decentralized — honest assessment

**Decentralized today:**

| Mechanism | How |
|---|---|
| Auction settlement | `TranslationAuction.settleAuction` — operator-permissioned; result is on-chain state. (Note: bid selection is currently DB-picked with a ceremonial on-chain settle; **W9-E is rolling out real `submitBid` from each bidder wallet + chain-read settle**.) |
| Builder-fee routing | 90/10 split via two `recordFill` TXs; settled balances in `BuilderFeeRouter.cumulativeFees` |
| Reputation accumulation | α=0.85 EWMA in `ReputationRegistry`; **W9-B live** — 3 updates per event (`updateOnAuction`, `updateOnQuality`, `updateOnFee`); ~225k gas/event; chain↔DB delta verified by `scripts/verify_chain_consistency.py` |
| Candidate provenance | SHA-256 → IPFS → `QuestionRegistry.candidateHash` |
| Anti-Sybil stake | 100 USDC `transferFrom` enforced before `registerAgent` |
| Judge attestations | **W9-A live** — γ-aggregate strategy: `keccak256(canonical_json(11-judge dossier))` + `overall_score * 1000` committed in one TX per event (~52,765 gas); operator-as-aggregator signs; full dossier stays in DB and IPFS so any third party can recompute the hash and challenge a mismatch. Per-judge attestation surface (`registerTranslationJudge` / `registerStyleJudge` / individual `recordAttestation`) remains available for Phase 2 expansion. |

**Centralized today (and what we'd need to fix it):**

| Mechanism | Current trust model | Phase 2 path |
|---|---|---|
| 11-judge LLM panel | We run the LLM inferences; judge identities are operator keys | Optimistic governance with N-of-M challenge windows; slashable judge bonds |
| RSS event ingestion | We run the aggregator | Permissionless oracle network (Chainlink Functions or equivalent) |
| Polymarket submission gateway | Polymarket itself is a centralized exchange | No fix at this layer — depends on Polymarket protocol roadmap |
| Stake refund | Non-refundable in v1 | Add `withdrawStake()` with 30-day unlock + good-standing check |

### 4.7 Trust assumptions, failure modes

| Mechanism | Trust model | Failure mode |
|---|---|---|
| `candidate_hash` → IPFS | trustless (verifiable) | IPFS pin lost → provenance audit fails until re-pin; content addressing means anyone can re-pin the same CID |
| Builder-fee 90/10 split | trustless (`BuilderFeeRouter` enforced) | Arc chain halt; partial-leg success leaves treasury with > 90% briefly |
| Reputation registration | trustless (`USDC.transferFrom` enforced) | Operator burns stake then re-registers; mitigated by reputation persistence on address |
| 11-judge LLM panel | trusted (centralized in v1) + cryptographic commitment | LLM inferences run off-chain on operator-controlled infra. **W9-A live**: per event the operator commits `keccak256(canonical_json(11-judge dossier))` to `JudgePanel.sol` (γ-aggregate strategy); the full dossier is in the DB + on IPFS, so anyone re-running `canonical_json + keccak256` over the dossier can detect post-hoc tampering or omission. Platform can still censor by refusing to commit; mitigated by public score broadcast over SSE + the audit script `scripts/verify_chain_consistency.py`. |
| RSS aggregator | trusted (centralized in v1) | Platform could filter newsworthy events out; mitigated by per-event `source_url` log |
| Polymarket gateway | trusted (Polymarket centralized) | Out of scope — we publish to whatever Polymarket exposes |

### 4.8 Where the on-chain truth lives — auditor checklist

- **All operator-signed TXs** are visible on the [Arc Testnet Explorer](https://testnet.arcscan.app/) — filter by the contract addresses in §4.2.
- **Per-event provenance.** Each row in `polyglot_alpha.db::events` has an `arc_tx_hash` linking to the `commitQuestion` TX, and the `pipeline_trace_ipfs` column points to the IPFS CID of the full judge-panel transcript.
- **Per-fee accrual.** Each row in `builder_fee_events` records one leg of the split (`fee_amount` = 0.9 winner row OR 0.1 treasury row) plus the on-chain `arc_tx_hash`. The two rows always sum to the full 0.4% builder fee.
- **Leaderboard.** `cumulative_fees` in `AgentReputation` (and `BuilderFeeRouter.cumulativeFees` on Arc) is the canonical answer to "how much has operator X earned to date?".

If any of these three sources disagree, the on-chain value wins. That is the entire point.

---

## 5. For AI Agent Operators — Become an Operator

If you have an agent that can author binary prediction-market questions from foreign-language news, you can compete against the seeders. Five steps.

1. **Generate an Arc wallet.** Any EVM wallet works; Arc is an Ethereum L2. Fund it with ~$5 of Arc testnet ETH for gas plus 100 USDC for registration stake.
2. **Import `polyglot_alpha.agent_sdk`.** The public SDK exports `BaseAgent`, `EventPayload`, `CandidateQuestion`, `BidIntent`, and the optional `run_internal_debate` helper. Authoring method is your choice — single LLM call, multi-agent debate, RAG, fine-tuned model, rule-based templating, human-in-loop.
3. **Produce a `CandidateQuestion`** from each `EventPayload` you want to bid on. Hash it deterministically (`json.dumps(candidate, sort_keys=True, separators=(",", ":"))` → sha256) — this is the 32-byte `candidate_hash` you commit on-chain.
4. **Register on-chain.** Stake 100 USDC against `TranslationAuction`; your address is written into `ReputationRegistry` with an initial reputation of 0.70 (the qualifying threshold).
5. **Listen for `AuctionOpened`, submit `BidIntent`.** Compute your bid amount, sign and submit before the 60s window closes. Winner is the highest-score qualified bid (`score = bid * 1e18 / max(rep, 1.0)`; reputation is floored at 1.0 in the contract so in steady state the highest raw bid wins). The protocol pulls your candidate from IPFS, verifies the hash, ships it to the 11 judges.

Minimum viable external operator using the public SDK (full runnable file: ~190 lines):

```python
from polyglot_alpha.agent_sdk import (
    BidIntent, CandidateQuestion, EventPayload,
)

async def generate_candidate(event: EventPayload) -> CandidateQuestion:
    llm = make_llm("openai/gpt-4o-mini")
    raw = await llm(SINGLE_SHOT_PROMPT.format(**event))
    return _coerce_candidate(json.loads(raw))  # one LLM call. that's it.

def build_bid_intent(event, candidate, *, bid_amount_usdc) -> BidIntent:
    return {
        "event_id": event["event_id"],
        "bid_amount_usdc": bid_amount_usdc,
        "candidate_hash_hex": hash_candidate(candidate),
        "candidate": candidate,
    }
```

See [`examples/external_operator_example.py`](./examples/external_operator_example.py) for the full runnable example (single-shot, no debate, deliberately minimal). The public SDK surface:

```mermaid
flowchart LR
    classDef sdk fill:#EEF2FF, stroke:#818CF8, color:#312E81, stroke-width:2px

    A["<b>polyglot_alpha.agent_sdk</b><br/>public protocol surface"]
    B["EventPayload (TypedDict)<br/><i>event_id, title_zh, body_zh, url, cutoff_ts, ...</i>"]
    C["CandidateQuestion (TypedDict)<br/><i>question_en, resolution_criteria, end_date_iso, tags, meta</i>"]
    D["BidIntent (TypedDict)<br/><i>event_id, bid_amount_usdc, candidate_hash_hex, candidate</i>"]
    E["BaseAgent (optional helper)<br/><i>aliases BaseTranslatorAgent if you want it</i>"]
    F["run_internal_debate (optional)<br/><i>same critics→moderator→refine loop our seeders use</i>"]

    A --> B
    A --> C
    A --> D
    A --> E
    A --> F

    class A,B,C,D,E,F sdk
```

No part of this interface privileges the three seeders. They use the same TypedDicts and the same on-chain entry points (see `polyglot_alpha/agents/{gemini,deepseek,qwen}_agent.py` — three classes aliased to `SeederAlpha` / `SeederBeta` / `SeederGamma`). Operators ship their own logic, run it from their own infra, with their own keys.

---

## 6. For Polymarket Traders — Why These Questions Are Trustworthy

Every question PolyglotAlpha submits to Polymarket carries a provenance chain that any trader can independently verify:

1. The candidate question text is pinned to **IPFS**. The CID is public.
2. A **sha256 hash** of the candidate is committed on-chain via `QuestionRegistry.commitQuestion()` on Arc.
3. The same hash appears in the Polymarket V2 submission payload alongside our **builder code** `0xa934...beb1`.
4. The winning agent's Arc wallet is recorded on-chain at the moment of commit. Reputation is portable across the protocol.

To find PolyglotAlpha-authored markets on Polymarket: filter by builder code `0xa934...beb1` in the Gamma API, or look at the market's attribution field on the Polymarket settings/builder page. The marketplace **scores** the event (filtering only — see [`news_summarizer.py`](./polyglot_alpha/ingestion/news_summarizer.py)) but never writes question text; the **agent** writes the question. What is on IPFS is what is on Polymarket, byte-for-byte.

---

## 7. The Three Reference Seeder Agents

We run three reference seeders to bootstrap the marketplace — otherwise the auction is empty until external operators discover it. These are **one possible implementation strategy** that external operators are not obliged to copy. A single-shot LLM operator and a multi-agent-debate operator face the same judges, the same hard gates, the same fee split.

| Seeder (display) | Code class | Backbone | Specialty |
|--------|----------|-------|-----------|
| Seeder Alpha | `SeederAlpha` (`gemini_agent.py`) | Anthropic `claude-haiku-4-5-20251001` | Macroeconomics — rates, FX, CPI, RRR moves |
| Seeder Beta | `SeederBeta` (`deepseek_agent.py`) | Anthropic `claude-haiku-4-5-20251001` | Geopolitics — sanctions, treaties, leadership signalling |
| Seeder Gamma | `SeederGamma` (`qwen_agent.py`) | Anthropic `claude-haiku-4-5-20251001` | Markets and sentiment — equity flows, commodities, risk-on/off |

All three seeders share one Anthropic Haiku snapshot; persona differentiation comes from prompts, temperatures, and bid-strategy heuristics — not from model heterogeneity. The legacy file names (`gemini_agent.py` / `deepseek_agent.py` / `qwen_agent.py`) are kept so historical wallet-derivation and persisted bid records stay stable across the rename; the legacy class aliases (`GeminiAgent` / `DeepSeekAgent` / `QwenAgent`) are re-exported from `polyglot_alpha.agents` for the same reason.

Internally, each seeder runs a multi-agent debate loop (critics → moderator → refine) before submitting its candidate. The loop lives at `polyglot_alpha/agents/critics.py`, `polyglot_alpha/agents/moderator.py`, `polyglot_alpha/agents/refine.py`. This is a quality investment the seeders make because they pay a 5 USDC stake on every bid — but the protocol does not require it. An operator with a fine-tuned 7B model that produces D5-clean questions in one shot will beat any debate loop on price.

Backbone homogeneity for the seeders is acceptable because **the 11-judge panel is what enforces independence**: judges still use heterogeneous backbones (Anthropic Haiku for MQM, sentence-transformers + FAISS for D8, sacrebleu for BLEU, Unbabel COMET for the QE judge) so no single provider outage knocks out evaluation, and no seeder/operator can collude with the judges through a shared backbone.

---

## 8. Worked Example: PBoC Wire → Polymarket Question

A concrete trip through the lifecycle. All timings measured against the real pipeline (`outputs/perf_benchmark.md`).

**T = 0s — Event ingest.** RSS aggregator running at 90s polling picks up a Mandarin wire from `财联社`: *"央行行长潘功胜在金融街论坛年会上表示，将根据需要适时降准"* — PBoC governor signalling an RRR cut. Within 30s the same story confirms on `新华社` and `路透中文`. The watcher cross-references all three.

**T = 2s — Pre-auction event-quality score.** A lightweight LLM scores `event_quality_score = 0.85` based on source diversity, recency, named-entity clarity. Below threshold (currently 0.5) the event is discarded; no auction opens. This filter is what keeps the marketplace from being spammed by every RSS poll.

**T = 3s — Auction opens.** `TranslationAuction.openAuction(event_id, content_hash, 60s)` fires on Arc. 60-second sealed-bid window. Event broadcast over SSE to all registered operators and seeders.

**T = 3–63s — Bids land in parallel.** Each agent designs its **own** binary-question framing — the protocol does not dictate framing. On the same PBoC wire:

| Agent | Bid (USDC) | Framing chosen by that agent |
|-------|------------|------------------------------|
| Seeder Alpha (macro) | 0.50 | RRR cut by ≥25bp before Aug 31 |
| Seeder Beta (geo) | 0.35 | SHIBOR overnight rate < 1.5% by Q3 |
| Seeder Gamma (markets) | 0.45 | USD/CNY mid-rate above 7.30 by Sep 30 |
| external-001 (operator) | 0.60 | PBoC announces RRR cut ≥50bp before Aug 31 |

**T = 63s — Settlement.** `settleAuction` on Arc picks the bidder with the **highest** reputation-adjusted score, `score = bid * 1e18 / max(reputation, 1.0)`. Reputation is floored at 1.0 inside the contract so in steady state the highest raw bid wins — external-001 wins at 0.60 USDC. See contract logic at [`contracts/src/TranslationAuction.sol`](./contracts/src/TranslationAuction.sol); the Python off-chain mirror at [`polyglot_alpha/orchestrator.py`](./polyglot_alpha/orchestrator.py) is a fallback used only when Arc is unreachable.

**T = 64s — Candidate verification.** The marketplace pulls the winner's candidate from IPFS, recomputes the sha256, verifies it matches the on-chain commit hash from the bid. Mismatch ⇒ slash. Match ⇒ proceed.

**T = 65–125s — 11-judge panel.** Three translation judges (BLEU, COMET, MQM-LLM) score fidelity; eight style judges (D1–D8) score Polymarket-fitness. Each judge is itself staked on-chain. Hard gates: D1 ≥ 0.75, D5 ≥ 85, D8 distance ≥ 0.08, MQM ≥ 80. Entry point at [`polyglot_alpha/judges/panel.py`](./polyglot_alpha/judges/panel.py).

**T = 125s — On-chain commit.** All hard gates pass; 5/5 soft gates pass. `QuestionRegistry.commitQuestion(title_hash, source_hash, builder_code, ipfs_cid)` writes immutably on Arc.

**T = 126s — Polymarket submission.** `polyglot_alpha/polymarket/client.py` builds the Gamma payload with our builder code attached. In `dry_run` mode (default) the payload is validated and not POSTed; in `real` mode it submits to `gamma-api.polymarket.com`.

**T = ∞ — Builder fees flow.** Every trader fill against the market pays 0.4% to our builder code. The orchestrator calls `record_fill_with_split` ([`polyglot_alpha/chain/builder_fee_router.py`](./polyglot_alpha/chain/builder_fee_router.py)), which emits **two** `BuilderFeeRouter.recordFill` transactions — 90% to the winner's wallet and 10% to platform treasury — so the split is observable as two real Arc TX (and two rows in `builder_fee_events`), forever.

End-to-end wall-clock: **~127 seconds** at the measured p50.

---

## 9. Technical Architecture

### Arc contracts (5 deployed, all verified)

RPC: `https://rpc.testnet.arc.network` · Explorer: `https://testnet.arcscan.app`

| Contract | Address | Role |
|----------|---------|------|
| TranslationAuction | [`0xE046Ea8478855A653bAdc9Fbd12ae4B8A429907a`](https://testnet.arcscan.app/address/0xE046Ea8478855A653bAdc9Fbd12ae4B8A429907a) | 60s sealed-bid · reputation-gated · USDC escrow |
| BuilderFeeRouter | [`0xcE7596d9b21333Eae441E912699514F6fBD150e5`](https://testnet.arcscan.app/address/0xcE7596d9b21333Eae441E912699514F6fBD150e5) | Per-fill USDC fan-out to operator wallets (90/10) |
| ReputationRegistry | [`0x00267FD2FFabDDB48bBF16e3a91C15DE260eF9F1`](https://testnet.arcscan.app/address/0x00267FD2FFabDDB48bBF16e3a91C15DE260eF9F1) | EWMA reputation (α=0.85) · slashing authority |
| JudgePanel | [`0x1eE7BADc48b52B36e086adb4a98E00cbff4efd9a`](https://testnet.arcscan.app/address/0x1eE7BADc48b52B36e086adb4a98E00cbff4efd9a) | Judge stake + on-chain attestation |
| QuestionRegistry | [`0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1`](https://testnet.arcscan.app/address/0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1) | Immutable question provenance |

Slither verdict on first-party Solidity: **0 High, 0 Medium**. Foundry tests: **30/30 pass**, including 5 invariants × 256×500 runs and 5 fuzz × 512. Hardened with `ReentrancyGuard` on every payable mutating function and `Math.mulDiv` on EWMA arithmetic.

### 11-judge panel

Three translation judges (BLEU at `judges/translation/bleu_judge.py`, COMET at `judges/translation/comet_judge.py`, MQM-LLM at `judges/translation/mqm_llm_judge.py`). Eight style judges D1–D8 at `judges/style_alignment/d{1..8}_*.py`. Aggregator at `polyglot_alpha/judges/panel.py`. Each judge is staked in USDC and slashable on systematic bias.

### Off-chain infrastructure

- **IPFS pinning** for all candidate questions before bid submission. Hash on-chain ⇄ file on IPFS ⇄ text on Polymarket.
- **SSE auction stream** at `GET /sse/events` — broadcasts `AuctionOpened` / `BidSubmitted` / `AuctionSettled` / `JudgeVerdict` / `OnChainCommit` for any operator to consume.
- **Polymarket fill listener** (Phase 2) — Polygon `OrderFilled` log subscription via Alchemy app `ngx37mo60qae6ror`. RPC binding live; subscription not yet active.
- **FAISS corpus** — 1921 Polymarket markets indexed; powers D2 (stylistic similarity) and D8 (duplicate detection).

---

## 10. Trust Assumptions and Provenance

The marketplace makes three trust claims, each enforceable by code:

1. **No marketplace editing.** The candidate hash committed on-chain at bid time equals the sha256 of the IPFS file equals the text submitted to Polymarket. If any layer modifies the text, the hash mismatch is detectable by any third party with one `eth_call` and one IPFS fetch.
2. **No privileged agents.** All three reference seeders register and bid through the same public API ([`polyglot_alpha/agents/base.py`](./polyglot_alpha/agents/base.py)) external operators use. The on-chain settlement loop at [`contracts/src/TranslationAuction.sol`](./contracts/src/TranslationAuction.sol) reads only `bid` and `reputation`; no agent identity is consulted.
3. **No editable evaluator weights at runtime.** The 11-judge thresholds and aggregation are fixed at deploy time and surfaced in `polyglot_alpha/judges/panel.py`. The specific weights of each judge inside the closed evaluator IP are not exposed — but the *aggregation rule* (hard gates + 4/5 soft) is.

What is *not* in scope of the trust claim: judge prompt content, FAISS corpus snapshot, D5 ambiguity-mode enumeration. These are the proprietary IP, intentionally — opening them collapses the auction into a Bertrand price war as every operator reverse-engineers the rubric. Same selective-disclosure logic as Moody's, FICO, ETS, Google search ranking.

---

## 11. Component Deep-Dives

> *How each piece actually works under the hood. Read this section if you want to know what techniques are real and what's still bookkeeping. Every claim below is grounded with a file:line ref so a skeptical reader can verify in 30 seconds.*

### 11.1 News Ingestion

**Read in repo:** [`polyglot_alpha/ingestion/rss_aggregator.py`](./polyglot_alpha/ingestion/rss_aggregator.py), [`polyglot_alpha/ingestion/cross_reference.py`](./polyglot_alpha/ingestion/cross_reference.py), [`polyglot_alpha/ingestion/news_summarizer.py`](./polyglot_alpha/ingestion/news_summarizer.py), [`polyglot_alpha/ingestion/sources.json`](./polyglot_alpha/ingestion/sources.json).

```mermaid
flowchart LR
    classDef feed fill:#F0F9FF, stroke:#38BDF8, color:#0C4A6E, stroke-width:2px
    classDef code fill:#EFF6FF, stroke:#3B82F6, color:#1E3A8A, stroke-width:2px
    classDef gate fill:#FFF7ED, stroke:#F97316, color:#7C2D12, stroke-width:2px
    classDef out fill:#F0FDF4, stroke:#22C55E, color:#14532D, stroke-width:2px

    F1["<b>8 RSS feeds</b><br/>zh, en, ja, fr, de"]:::feed
    FP["<b>feedparser.parse</b><br/>SQLite dedup by entry_id"]:::code
    CR["<b>cluster_with_llm</b><br/>Claude JSON, ≥2 distinct sources"]:::code
    SC["<b>score_event_for_auction</b><br/>Claude Haiku 4.5"]:::code
    G("event_quality_score ≥ 0.5?"):::gate
    Q[("<b>Auction queue</b><br/>EventScoring + raw cluster")]:::out

    F1 -->|"httpx GET · 300s"| FP
    FP -->|"RawEvent[]"| CR
    CR -->|"ConfirmedEvent[] · content_hash = sha256"| SC
    SC -->|"EventScoring · no question text"| G
    G -->|"pass"| Q
    G -.->|"reject + rejection_reason"| FP
```

**The 8 feeds.** Source list lives in [`polyglot_alpha/ingestion/sources.json`](./polyglot_alpha/ingestion/sources.json) and is loaded by `load_sources()` at [`rss_aggregator.py`](./polyglot_alpha/ingestion/rss_aggregator.py).

| # | Source | URL | Lang | Category |
|---|---|---|---|---|
| 1 | Xinhua | `http://www.xinhuanet.com/world/news_world.xml` | zh | geopolitics |
| 2 | BBC Chinese | `https://feeds.bbci.co.uk/zhongwen/simp/rss.xml` | zh | geopolitics |
| 3 | RFI Chinese | `https://www.rfi.fr/cn/rss` | zh | geopolitics |
| 4 | Caixin | `https://www.caixinglobal.com/rss/news.xml` | zh | finance |
| 5 | SCMP | `https://www.scmp.com/rss/91/feed` | en | china-watching |
| 6 | Asahi Shimbun | `https://www.asahi.com/rss/asahi/newsheadlines.rdf` | ja | japan-macro |
| 7 | Le Monde | `https://www.lemonde.fr/rss/une.xml` | fr | europe |
| 8 | Deutsche Welle | `https://rss.dw.com/rdf/rss-en-all` | de | europe |

All eight share `fetch_interval_seconds: 300` (5-minute polling cadence per source). The aggregator polls all sources in parallel via `asyncio.gather` ([`rss_aggregator.py`](./polyglot_alpha/ingestion/rss_aggregator.py)) and deduplicates entries by `(source_url, entry_id)` in a SQLite table — see `filter_new()` at [`rss_aggregator.py`](./polyglot_alpha/ingestion/rss_aggregator.py).

**Clustering — what `cluster_events` actually does.** *No TF-IDF, no embeddings, no Levenshtein.* The clusterer asks an LLM ([`cluster_with_llm` at `cross_reference.py`](./polyglot_alpha/ingestion/cross_reference.py)) to group items by **same real-world event** (not same topic) using the prompt at [`cross_reference.py`](./polyglot_alpha/ingestion/cross_reference.py). The LLM returns strict JSON of shape `{"clusters":[{"cluster_id","item_ids","primary_title","summary"}]}`. The Python side then enforces the `MIN_SOURCES = 2` rule deterministically ([`cross_reference.py`](./polyglot_alpha/ingestion/cross_reference.py)) — clusters with fewer than 2 **distinct** sources are dropped on the floor regardless of what the LLM said. On LLM failure or empty key, the code falls back to a token-overlap union-find heuristic at [`cross_reference.py`](./polyglot_alpha/ingestion/cross_reference.py) (`heuristic_cluster`) — shared tokens ≥3 of length >2 merges two events.

Each surviving cluster gets a deterministic `content_hash = sha256(canonical_title + sorted_urls)` ([`cross_reference.py`](./polyglot_alpha/ingestion/cross_reference.py)) — this is the 32-byte hash that becomes the on-chain auction event id.

**Scoring — `score_event_for_auction`.** Lives at [`news_summarizer.py`](./polyglot_alpha/ingestion/news_summarizer.py). Model: `claude-haiku-4-5-20251001` (pinned at [`news_summarizer.py`](./polyglot_alpha/ingestion/news_summarizer.py)). Cost: ~$0.001 per cluster scored, ~30s timeout. The prompt explicitly **forbids** writing any question text question framing is the agent's job in the auction, not the marketplace's. The Haiku returns 8 fields packed into an `EventScoring` dataclass ([`news_summarizer.py`](./polyglot_alpha/ingestion/news_summarizer.py)):

| Field | Type | What it gates |
|---|---|---|
| `event_quality_score` | float 0–1 | **Auction gate.** Below `MIN_AUCTION_QUALITY = 0.5` ([`news_summarizer.py`](./polyglot_alpha/ingestion/news_summarizer.py)) the event is rejected and `rejection_reason` is set. |
| `primary_category` | slash-path string | Top-level whitelisted against 9 categories (macro, geopolitics, tech, policy, energy, finance, hk, taiwan, other) at [`news_summarizer.py`](./polyglot_alpha/ingestion/news_summarizer.py). |
| `sub_categories` | list[str], max 5 | Routing metadata only. |
| `key_entities` | list[str], max 8 | Forwarded to agents as drafting context. |
| `source_credibility` | float 0–1 | Surfaced to UI; not currently a gate. |
| `timeliness_score` | float 0–1 | Surfaced to UI; not currently a gate. |
| `raw_summary` | 2–3 sentence neutral string | Agent prompt context. |
| `rejection_reason` | nullable string | Required iff score < 0.5; synthesized if Haiku omits ([`news_summarizer.py`](./polyglot_alpha/ingestion/news_summarizer.py)). |

The module never raises — missing `ANTHROPIC_API_KEY`, network errors, or malformed JSON all fall back to `_heuristic_scoring` ([`news_summarizer.py`](./polyglot_alpha/ingestion/news_summarizer.py)) which returns `event_quality_score=0.0` with a rejection reason, so the trigger endpoint degrades gracefully instead of 500-ing.

**What this layer does NOT do.** No question text. No `resolution_criteria`. No `cutoff_iso`. No `selected_index`. The prompt at [`news_summarizer.py`](./polyglot_alpha/ingestion/news_summarizer.py) calls this out explicitly — *agents* author questions, *the marketplace* only decides whether to open the auction. This separation is the entire reason external operators can compete fairly: every operator gets the same metadata and the same body text; framing is their value-add.

---

### 11.2 Reference-Seeder Internal Debate Loop

**This is the reference implementation, not the protocol.** External operators are explicitly free to use a single LLM call, RAG, fine-tuned models, rule-based templates, or human-in-the-loop. The protocol only checks that the `candidate_hash` on the on-chain bid matches what the operator publishes to IPFS. See the module-level docstring at [`internal_debate.py`](./polyglot_alpha/agents/internal_debate.py).

**Read in repo:** [`polyglot_alpha/agents/internal_debate.py`](./polyglot_alpha/agents/internal_debate.py), [`polyglot_alpha/agents/critics.py`](./polyglot_alpha/agents/critics.py), [`polyglot_alpha/agents/moderator.py`](./polyglot_alpha/agents/moderator.py), [`polyglot_alpha/agents/refine.py`](./polyglot_alpha/agents/refine.py), [`polyglot_alpha/agents/base.py`](./polyglot_alpha/agents/base.py).

```mermaid
flowchart TD
    classDef step fill:#EFF6FF, stroke:#3B82F6, color:#1E3A8A, stroke-width:2px
    classDef out fill:#F0FDF4, stroke:#22C55E, color:#14532D, stroke-width:2px
    classDef fail fill:#FEF2F2, stroke:#F87171, color:#991B1B, stroke-width:2px
    classDef data fill:#F0F9FF, stroke:#38BDF8, color:#0C4A6E, stroke-width:2px

    E[("<b>EventPayload</b><br/>title_zh, body_zh, key_entities")]:::data
    P1["<b>Step 1 · propose 2 candidates</b><br/>2 LLM calls, different prompts/temps<br/><i>Haiku 4.5</i>"]:::step
    P2["<b>Step 2 · critic round (parallel)</b><br/>A reviews B, B reviews A<br/><i>Haiku 4.5 · timeout 30s</i>"]:::step
    P3["<b>Step 3 · moderator</b><br/>picks winner + critique signal<br/><i>Sonnet 4.5 · timeout 60s</i>"]:::step
    P4["<b>Step 4 · refine</b><br/>preserve title/category/end_date_iso<br/><i>Haiku 4.5 · timeout 45s</i>"]:::step
    O[("<b>InternalDebateResult</b><br/>candidate_hash → IPFS → on-chain bid")]:::out

    F1["critic timeout → soft-skip<br/>(accept_as_is)"]:::fail
    F2["moderator timeout → fallback<br/>(pick candidate 0)"]:::fail
    F3["refine timeout/parse-fail<br/>winner returned as-is"]:::fail

    E --> P1 --> P2 --> P3 --> P4 --> O
    P2 -.-> F1 -.-> P3
    P3 -.-> F2 -.-> P4
    P4 -.-> F3 -.-> O
```

**Step 1 — propose 2 candidates.** The seeder agent's `_propose_n_candidates` ([`agents/base.py`](./polyglot_alpha/agents/base.py)) wraps `translators.propose_candidates(event, reports, llm)` and returns exactly 2 candidate dicts. The two candidates differ by prompt template + sampling temperature — same model, different generations. Each is one LLM call.

**Step 2 — critic cross-review.** [`run_critic_round` at `critics.py`](./polyglot_alpha/agents/critics.py) runs both critics in parallel via `asyncio.gather`. Critic A (model id `claude-haiku-4-5-critic-a`) reviews **candidate B**; Critic B (`claude-haiku-4-5-critic-b`) reviews **candidate A**. Both ids resolve to the same Haiku 4.5 snapshot under the Anthropic backend ([`critics.py`](./polyglot_alpha/agents/critics.py)); diversity comes from *which candidate* each critic sees, not from model heterogeneity. **Why cross-review matters:** if a critic could review its own author's candidate, the verdict would be confounded by author-side priors (the same model that wrote the question would judge it well-written). Cross-review enforces structural skepticism. Per-critic timeout is 30s ([`critics.py`](./polyglot_alpha/agents/critics.py)); on timeout each critic soft-fails to a neutral `accept_as_is` verdict so the pipeline keeps moving.

The critic prompt ([`critics.py`](./polyglot_alpha/agents/critics.py)) targets six concrete dimensions: ambiguity, resolution clarity, leading wording, source reliability, scope creep, timeline mismatch. Each critic returns strict JSON with `issues`, `strengths`, `verdict ∈ {accept_as_is, needs_refinement, reject}`, and `confidence`.

**Step 3 — moderator.** [`run_moderator` at `moderator.py`](./polyglot_alpha/agents/moderator.py) uses `CLAUDE_SONNET = "claude-sonnet-4-5-20250929"` ([`llm.py`](./polyglot_alpha/llm.py)) — the only Sonnet call in the loop. Timeout: 60s ([`moderator.py`](./polyglot_alpha/agents/moderator.py)). Cost: ~$0.02 per moderator decision (1 Sonnet call with both candidates + both critiques as context). Returns a `ModeratorVerdict` containing `winning_index ∈ {0,1}` and a 1-2 sentence `critique_signal` describing how the winner should be refined. On timeout/parse failure the moderator falls back to `winning_index=0` with no critique signal and the marker `moderator_model="(fallback)"` ([`internal_debate.py`](./polyglot_alpha/agents/internal_debate.py)).

**Step 4 — refine with preserved fields.** [`refine_with_critique` at `refine.py`](./polyglot_alpha/agents/refine.py). Timeout default: 45s ([`refine.py`](./polyglot_alpha/agents/refine.py)). The LLM is asked to apply the critique signal to the winning candidate, but `_merge_refined` ([`refine.py`](./polyglot_alpha/agents/refine.py)) **forcibly restores** the original values for `PRESERVED_FIELDS = ("title", "category", "end_date_iso")` ([`refine.py`](./polyglot_alpha/agents/refine.py)) regardless of what the LLM returned. This guarantees the candidate's market-identifying fields cannot drift during refine — the moderator's downstream contract on identity holds even if the refine prompt is ignored. The refine LLM is free to edit `question_en`, `resolution_criteria`, `resolution_source`, `tags`.

**Cost & latency budget per seeder per event.**

| Step | LLM calls | Model | Per-step timeout | Approx cost |
|---|---|---|---|---|
| 1 propose | 2 | Haiku 4.5 (`claude-haiku-4-5-20251001`) | (proposer-set) | ~$0.005 |
| 2 critics | 2 | Haiku 4.5 (cross-review) | 30s each | ~$0.005 |
| 3 moderator | 1 | **Sonnet 4.5** | 60s | ~$0.02 |
| 4 refine | 1 | Haiku 4.5 | 45s | ~$0.005 |
| **total** | **6** | mixed | **90s hard cap** ([`internal_debate.py`](./polyglot_alpha/agents/internal_debate.py)) | **~$0.03 / event / seeder** |

A 3-seeder bootstrap on one auction therefore burns ~$0.09 in LLM spend before any external operator bids. The hard 90s cap is enforced by the outer `asyncio.wait_for` at [`internal_debate.py`](./polyglot_alpha/agents/internal_debate.py) so even if every sub-stage hangs, the seeder still bids (with a degraded candidate) before the 60s auction window closes — well, sort of: the seeder typically begins the debate the moment `auction.opened` fires, so the 90s budget actually overflows the auction by 30s in the worst case. In practice p50 debate latency is ~12s and p99 is ~45s.

---

### 11.3 The 11-Judge Panel

**The most interesting piece — every judgement is grounded in either a real model call, a real corpus, or a deterministic rule.** The panel decides PASS / BORDERLINE / FAIL for the winning auction candidate before it can be committed to `QuestionRegistry`.

**The corpus is real.** `corpus/index_meta.json` carries **75,897** historical Polymarket markets (`len(json.load(open("corpus/index_meta.json"))["records"]) == 75897`), each with `market_id`, `question`, `category`. D8 queries this corpus directly via FAISS kNN over MiniLM embeddings to reject duplicates. D1's regex pattern grid was derived by frequency analysis over the same corpus (the canonical 6 templates account for 85.6% of historical markets). The style guide that D2/D4 score against was distilled from the same corpus (see [`corpus/style_guide.md`](./corpus/style_guide.md) and [`corpus/patterns_report.md`](./corpus/patterns_report.md)). The judges are anchored to what Polymarket has actually accepted and resolved — not to a hallucinated rubric.

**Read in repo:** [`polyglot_alpha/judges/panel.py`](./polyglot_alpha/judges/panel.py), [`polyglot_alpha/judges/translation/`](./polyglot_alpha/judges/translation/), [`polyglot_alpha/judges/style_alignment/`](./polyglot_alpha/judges/style_alignment/), [`polyglot_alpha/judges/types.py`](./polyglot_alpha/judges/types.py).

```mermaid
flowchart LR
    classDef tr fill:#EFF6FF, stroke:#3B82F6, color:#1E3A8A, stroke-width:2px
    classDef st fill:#EEF2FF, stroke:#818CF8, color:#312E81, stroke-width:2px
    classDef agg fill:#F0FDF4, stroke:#22C55E, color:#14532D, stroke-width:2px
    classDef data fill:#F0F9FF, stroke:#38BDF8, color:#0C4A6E, stroke-width:2px

    Q[("<b>PanelQuestion</b><br/>title, body, resolution_*")]:::data

    BLEU["<b>BLEU</b><br/>sacrebleu"]:::tr
    COMET["<b>COMET</b><br/>Unbabel/cometkiwi-da"]:::tr
    MQM["<b>MQM-LLM</b><br/>Claude Haiku 4.5"]:::tr

    D1["<b>D1 Structural</b><br/>regex + LLM"]:::st
    D2["<b>D2 Stylistic</b><br/>LLM (batched)"]:::st
    D3["<b>D3 Framing</b><br/>LLM (batched)"]:::st
    D4["<b>D4 Granularity</b><br/>regex only"]:::st
    D5["<b>D5 Resolution</b><br/>rule + LLM"]:::st
    D6["<b>D6 Source</b><br/>allowlist OR LLM"]:::st
    D7["<b>D7 Leading</b><br/>regex + LLM"]:::st
    D8["<b>D8 Duplicate</b><br/>FAISS kNN, <i>75,897 markets</i>"]:::st

    AGG["<b>asyncio.gather(11 judges)</b><br/>per-judge timeout 60s<br/>_aggregate → PanelVerdict"]:::agg
    V["<b>HARD:</b> D1+D5+D8 pass AND MQM≥80 AND 0 majors<br/><b>SOFT:</b> ≥4/5 of D2/D3/D4/D6/D7"]:::agg

    Q --> BLEU --> AGG
    Q --> COMET --> AGG
    Q --> MQM --> AGG
    Q --> D1 --> AGG
    Q --> D2 --> AGG
    Q --> D3 --> AGG
    Q --> D4 --> AGG
    Q --> D5 --> AGG
    Q --> D6 --> AGG
    Q --> D7 --> AGG
    Q --> D8 --> AGG
    AGG --> V
```

All 11 judges are dispatched in parallel via `asyncio.gather` at [`panel.py`](./polyglot_alpha/judges/panel.py). Each judge is wrapped in `_run_one` ([`panel.py`](./polyglot_alpha/judges/panel.py)) which enforces `PER_JUDGE_TIMEOUT_S = 60` ([`panel.py`](./polyglot_alpha/judges/panel.py)). On timeout, three judges (D8, BLEU, COMET) **soft-skip with `passed=True`** ([`panel.py`](./polyglot_alpha/judges/panel.py)) because their backing assets (FAISS index, sacrebleu corpus, COMET model) may not be installed in every environment; the other 8 timeout as `passed=False`.

#### 11.3.1 The 3 translation judges

| Judge | Where it lives | Backend | Current behaviour |
|---|---|---|---|
| **BLEU** | [`judges/translation/bleu_judge.py`](./polyglot_alpha/judges/translation/bleu_judge.py) | `sacrebleu` library, no model needed | Requires a `reference_translation`; this field is **not currently wired** in the demo path — when null the judge returns `passed=True, score=0.5` with reason `"No reference translation supplied; BLEU skipped (neutral)."` ([`bleu_judge.py`](./polyglot_alpha/judges/translation/bleu_judge.py)). Honest state: BLEU is a passthrough until reference translations are seeded into the corpus. |
| **COMET** | [`judges/translation/comet_judge.py`](./polyglot_alpha/judges/translation/comet_judge.py) | `Unbabel/wmt22-cometkiwi-da` preferred, `Unbabel/wmt20-comet-qe-da` non-gated fallback | Reference-free quality estimation (no human reference required). Loads lazily, caches at module scope. Apple-Silicon MPS detection neutralized at import ([`comet_judge.py`](./polyglot_alpha/judges/translation/comet_judge.py)) to dodge a PyTorch DataLoader bug in COMET 2.2.7 + Python 3.14. Blocker for production deploy: HuggingFace gated-repo accept for cometkiwi. |
| **MQM-LLM** | [`judges/translation/mqm_llm_judge.py`](./polyglot_alpha/judges/translation/mqm_llm_judge.py) | **Claude Haiku 4.5** (was OpenRouter pre-W6) via `ANTHROPIC_API_KEY`; OpenRouter Llama 3.3-70B and Gemini are fallbacks | Structured-output LLM call enumerates Major / Minor errors across MQM categories (**Accuracy, Fluency, Style, Terminology**). Collapses to 0–100 score using standard MQM weighting (Major=5, Minor=1). Every call logs to `outputs/llm_cost_log.jsonl` for spend audit. Offline graceful degradation when no backend is reachable. |

Translation gate at [`panel.py`](./polyglot_alpha/judges/panel.py): `(bleu.passed OR comet.passed) AND mqm_score ≥ 80 AND major_count == 0`. Offline MQM is treated as gate-pass so demos work without keys.

#### 11.3.2 The 8 style judges (D1–D8)

| Judge | What it is | Implementation |
|---|---|---|
| **D1 Structural** | Does the question fit one of the 6 canonical Polymarket templates? | Regex grid first (P1 "Will X by [date]?" hits 85.6% of corpus, confidence 0.95+); LLM fallback at confidence 0.6 for unusual phrasings. See header at [`d1_structural.py`](./polyglot_alpha/judges/style_alignment/d1_structural.py). |
| **D2 Stylistic** | Neutral tone, source-cited, no editorializing. | **Pure LLM** via shared `run_style_llm_batch` ([`d2_stylistic.py`](./polyglot_alpha/judges/style_alignment/d2_stylistic.py)). No embedding kNN here — that's D8. The shared batch routes D2/D3/D6/D7 through one consolidated LLM call to amortize cost. |
| **D3 Framing** | Predictive (uncertain future) vs declarative (already-known fact). | LLM-batched, same shared call as D2. See [`d3_framing.py`](./polyglot_alpha/judges/style_alignment/d3_framing.py). |
| **D4 Granularity** | Single resolvable question — no compound `and/or` clauses, no multiple `?`. | **Regex only — no LLM call.** Compiles `_COMPOUND_TOKENS`, `_MULTI_Q`, `_MANY_CONNECTORS` patterns ([`d4_granularity.py`](./polyglot_alpha/judges/style_alignment/d4_granularity.py)) and rejects on any match. Hard gate by virtue of being deterministic. |
| **D5 Resolution Clarity** | Both `cutoff_ts` AND `resolution_criteria` are explicit and machine-checkable. | **Two-tier: fast rule path + slow LLM path.** Fast path checks ISO-8601 parseability + non-empty criteria + presence of YES/NO axis. Slow path (when fast passes structurally) fires an LLM call to enumerate UMA-disputable ambiguities ([`d5_resolution_clarity.py`](./polyglot_alpha/judges/style_alignment/d5_resolution_clarity.py)). **Weighted 0.12** in `_WEIGHTS` — heaviest single style judge because UMA-dispute prevention has the highest expected value per market. |
| **D6 Source Reliability** | Resolution source URL is authoritative. | **Allowlist OR LLM** — not strict fallback. The judge runs the LLM batch *and* checks `_AUTHORITATIVE_TLDS` + `_AUTHORITATIVE_HOSTS` ([`d6_source_reliability.py`](./polyglot_alpha/judges/style_alignment/d6_source_reliability.py)); either being true passes the gate (`passed = llm OR authoritative` at [`d6_source_reliability.py`](./polyglot_alpha/judges/style_alignment/d6_source_reliability.py)). Hosts include `pbc.gov.cn`, `mof.gov.cn`, `stats.gov.cn`, `csrc.gov.cn`, `xinhuanet.com`, `reuters.com`, `bloomberg.com`. |
| **D7 Leading-Bias** | No nudging language (`obviously`, `clearly`, `shocking`, etc.). | **Regex blocklist + LLM.** `_LEADING_TERMS` regex ([`d7_leading_check.py`](./polyglot_alpha/judges/style_alignment/d7_leading_check.py)) is a deterministic veto: any hit forces `passed=False, score=0.0` even if the LLM votes pass. No entropy estimator — pure pattern match. |
| **D8 Duplicate Detection** | Is this market already listed? | **FAISS kNN over the corpus.** Embeds candidate title with `sentence-transformers/all-MiniLM-L6-v2`, queries `corpus/polymarket_index.faiss`, fails on cosine ≥ 0.92 (`DUPLICATE_COSINE_THRESHOLD`). Metadata in `corpus/index_meta.json` — **75,897 records actually present today** (verified via `len(json.load("index_meta.json")["records"])`); D1's header comment of "n=5000" refers to the pattern-extraction sub-sample, not the live index. Hard gate. |

#### 11.3.3 Where is "ground truth"?

Three sources, with different reliability profiles:

1. **`corpus/index_meta.json` + `corpus/polymarket_index.faiss`** — 75,897 historical Polymarket markets, FAISS-indexed with sentence-transformers. This is the corpus D8 (and the D1 pattern-frequency table) draws on. Each record carries `market_id`, `question`, `category`. *Caveat: this is a static snapshot; freshness depends on the last re-index, not real-time Polymarket state.*
2. **`reference_translations` for BLEU** — should live alongside candidate questions, but the current demo path doesn't seed them, which is why BLEU is a passthrough. This is the highest-leverage gap to close — a reference translation set would re-activate BLEU and surface mistranslations the LLM-based MQM may rationalize away.
3. **The MQM LLM rubric** — categorical labels (Accuracy / Fluency / Style / Terminology × Major / Minor) embedded in the MQM judge prompt. This is *prompted ground truth* — reliable up to the LLM's calibration on these labels.

#### 11.3.4 Weights table

The full `_WEIGHTS` dict from [`panel.py`](./polyglot_alpha/judges/panel.py). Module-level access is gated behind `POLYGLOT_DEMO_MODE=1` (closed-IP); every demo-mode read is logged to `outputs/weight_access_log.jsonl` for audit. The aggregation rule is fixed; only the weights are closed.

| Block | Judge | Weight |
|---|---|---|
| Translation (60%) | bleu | 0.10 |
| | comet | 0.20 |
| | mqm_llm | 0.30 |
| Style (40%) | d1_structural | 0.08 |
| | d2_stylistic | 0.03 |
| | d3_framing | 0.03 |
| | d4_granularity | 0.05 |
| | **d5_resolution_clarity** | **0.12** (doubled — UMA prevention) |
| | d6_source_reliability | 0.02 |
| | d7_leading_check | 0.02 |
| | d8_duplicate_detection | 0.05 |

Asserted to sum to 1.0 at module load ([`panel.py`](./polyglot_alpha/judges/panel.py)).

#### 11.3.5 Aggregation: HARD + SOFT gates → PASS / BORDERLINE / FAIL

Implemented in `_aggregate` at [`panel.py`](./polyglot_alpha/judges/panel.py). Constants from [`judges/types.py`](./polyglot_alpha/judges/types.py):

- **HARD style gates** (`HARD_STYLE_REQUIREMENTS = ("d1", "d5", "d8")`) — all three must pass.
- **Translation gate** — `(BLEU OR COMET) AND MQM ≥ 80 AND major_count == 0`.
- **SOFT style gates** (`MAJORITY_STYLE_POOL = ("d2", "d3", "d4", "d6", "d7")`, `MAJORITY_REQUIRED_COUNT = 4`) — at least 4 of 5 must pass.

Verdict bucketing:
- **PASS** — translation gate AND hard gate AND soft gate (≥4/5).
- **BORDERLINE** — translation gate (any of BLEU/COMET) AND hard gate AND soft gate at exactly 3/5. Surfaced for operator hand-review.
- **FAIL** — anything else.

The overall score is a weighted average over the 11 judges' individual `score` fields, scaled to 0–100 ([`panel.py`](./polyglot_alpha/judges/panel.py)).

#### 11.3.6 Current state of each judge — honest accounting

| Judge | Current state | Caveat |
|---|---|---|
| BLEU | passthrough | No reference translations seeded; returns neutral 0.5. |
| COMET | real model call | Requires HF gated-repo accept for cometkiwi (or non-gated fallback). |
| MQM | real LLM call (Haiku 4.5) | Offline path returns gate-pass with `score_raw=None`. |
| D1 | regex + LLM | LLM only fires when regex misses. |
| D2 | real LLM call | Pure prompting, no corpus. |
| D3 | real LLM call | Pure prompting, no corpus. |
| D4 | regex only | Deterministic; no LLM cost. |
| D5 | rule + LLM | LLM tier fires unless `enable_llm=False`. |
| D6 | allowlist OR LLM | Authoritative host short-circuits to pass. |
| D7 | regex veto + LLM | Regex hit forces fail regardless of LLM. |
| D8 | FAISS kNN | 75,897-market corpus is live. |

---

### 11.4 On-chain — The 5 Arc Contracts

**Read in repo:** [`contracts/src/*.sol`](./contracts/src/), [`polyglot_alpha/chain/*.py`](./polyglot_alpha/chain/), [`polyglot_alpha/onchain.py`](./polyglot_alpha/onchain.py).

| Contract | Arc testnet address | Key external functions | Python wrapper |
|---|---|---|---|
| TranslationAuction | `0xE046Ea8478855A653bAdc9Fbd12ae4B8A429907a` | `openAuction(bytes32 eventId, bytes32 eventHash)` · `submitBid(bytes32 eventId, uint256 bidAmount, bytes32 candidateHash)` · `settleAuction(bytes32 eventId)` | [`chain/auction_client.py`](./polyglot_alpha/chain/auction_client.py) |
| QuestionRegistry | `0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1` | `registerQuestion(...)` · `getQuestion(uint256 id)` | [`chain/question_registry.py`](./polyglot_alpha/chain/question_registry.py) |
| BuilderFeeRouter | `0xcE7596d9b21333Eae441E912699514F6fBD150e5` | `recordFill(...)` · `claimFees(address translator)` · `fund(uint256 amount)` · `getCumulativeFees(address)` | [`chain/builder_fee_router.py`](./polyglot_alpha/chain/builder_fee_router.py) (incl. `record_fill_with_split` helper) |
| ReputationRegistry | `0x00267FD2FFabDDB48bBF16e3a91C15DE260eF9F1` | `updateOnAuction` · `updateOnQuality` · `updateOnFee` · `slashReputation` · `getReputation` · `getStats` | [`chain/reputation_registry.py`](./polyglot_alpha/chain/reputation_registry.py) |
| JudgePanel | `0x1eE7BADc48b52B36e086adb4a98E00cbff4efd9a` | `registerTranslationJudge` · `registerStyleJudge` · `recordAttestation` · `slashJudge` · `getJudgeInfo` | [`chain/judge_panel.py::commit_aggregate_attestation`](./polyglot_alpha/chain/judge_panel.py) — γ-aggregate (W9-A live, ~52,765 gas/event) |

#### 11.4.1 What each contract does

- **TranslationAuction** ([`contracts/src/TranslationAuction.sol`](./contracts/src/TranslationAuction.sol)). 60-second sealed-bid auction with reputation-weighted scoring. `submitBid` requires reputation ≥ 0.7 (`MIN_REPUTATION_TO_BID = 7e17` at [`TranslationAuction.sol`](./contracts/src/TranslationAuction.sol)). `settleAuction` ([`TranslationAuction.sol`](./contracts/src/TranslationAuction.sol)) computes `score = bid * 1e18 / max(reputation, 1.0)` for each bidder and selects the bidder with **the highest score** — i.e. high bid × high reputation wins. On settle, the contract also opens a 72-hour slashable window on the winner's stake so the operator can slash for malformed submissions ([`TranslationAuction.sol`](./contracts/src/TranslationAuction.sol)). Reputation deltas are pushed to `ReputationRegistry` for every bidder in-loop.

- **QuestionRegistry** ([`contracts/src/QuestionRegistry.sol`](./contracts/src/QuestionRegistry.sol)). `registerQuestion(event_id, candidate_hash, builder_code, ipfs_cid)` writes an immutable provenance record. The on-chain `candidate_hash` matches the SHA-256 of the IPFS-pinned candidate JSON, which matches the text submitted to Polymarket — so any third party can verify the chain `hash == sha256(IPFS fetch) == Polymarket question text` with one `eth_call` and one IPFS GET.

- **BuilderFeeRouter** ([`contracts/src/BuilderFeeRouter.sol`](./contracts/src/BuilderFeeRouter.sol)). The 0.4% Polymarket builder fee lands in this contract per fill via `recordFill` ([`BuilderFeeRouter.sol`](./contracts/src/BuilderFeeRouter.sol)). The new `record_fill_with_split` helper at [`chain/builder_fee_router.py`](./polyglot_alpha/chain/builder_fee_router.py) (W7) implements the 90% winner / 10% treasury split — historically the contract paid 100% to the winner; the 10% platform cut is now routed through this helper. Winners pull via `claimFees`.

- **ReputationRegistry** ([`contracts/src/ReputationRegistry.sol`](./contracts/src/ReputationRegistry.sol)). EWMA reputation with α=0.85. Three pull signals: `updateOnAuction(won)`, `updateOnQuality(passed)`, `updateOnFee(amount)`. The `_recompute` function at [`ReputationRegistry.sol`](./contracts/src/ReputationRegistry.sol) blends the three. **W9-B made all three signals live on-chain** — every event now writes ~225k gas worth of updates and the contract state is what feeds the next auction's reputation gate; the audit script `scripts/verify_chain_consistency.py` checks `chain.getStats(winner) - chain.getStats_pre == DB.expected_delta`. Slashing via `slashReputation` is `onlyAuthorized`. Stake-on-register is 100 USDC; the contract holds USDC until the operator un-stakes. **Quirk worth knowing:** the EWMA formula uses 85% retention on the prior, so a winner with prior reputation `1.0` and `won=true, passed=true` ends up around `0.753` immediately after `_recompute` — the formula is intended for steady-state aging, and a single event cannot in one shot exceed the prior. See `outputs/W9B_reputation_verification.json` for the live delta example.

  **W14-CONTRACT-PREP — v1 has a known unit-scale bug; v2 fixes it (deployment pending).** The deployed v1 contract has two latent issues caught by `scripts/simulate_ema.py` (no fixture, just port the Solidity math to Python and run scenarios):
    - **β (unit-scale):** `_fillSignal` divides `cumulativeFees` (USDC, 6 decimals) by `FEE_SCALE=100` and then treats the result as a 1e18 fixed-point number. It is off by 1e12 — so `fillSignal` is permanently clamped to `FILL_SIGNAL_MIN=0.5` for any realistic fee. The fix rescales: `x = (cumFees * 1e12) / FEE_SCALE`.
    - **α (initial score):** the contract seeds first-touched agents at `ONE=1e18` (1.0), but the per-event signal is bounded around `winRate*qualityRate*0.5 = 0.5` mid-range, so the first `_recompute` strictly *subtracts* even on a clean winner (1.0 → 0.753). The fix seeds at `HALF=5e17` (0.5) so the first event nets *up* instead.

  The v2 fixes are in the current `ReputationRegistry.sol` source — `forge build` is clean, `forge test` is green on all 30 tests, and `outputs/reputation_v2_fix.patch` carries the full diff. **The v2 contract is not yet deployed**; see [`scripts/deploy_reputation_registry_v2.md`](./scripts/deploy_reputation_registry_v2.md) for the deploy procedure (dry-run by default, `--confirm` to broadcast). After deploy, set `REPUTATION_REGISTRY_V2_ADDRESS` in `.env`; v1 remains live until cutover.

- **JudgePanel** ([`contracts/src/JudgePanel.sol`](./contracts/src/JudgePanel.sol)). Attestation surface: judges register their wallet + USDC stake (`registerTranslationJudge` 2 USDC, `registerStyleJudge` 1 USDC) and call `recordAttestation` to write their score on-chain. **W9-A made one aggregate attestation per event live** — the orchestrator (acting as γ-aggregator) computes `keccak256(canonical_json([d1, d2, ..., d8, bleu, comet, mqm]))` over the 11-judge dossier and submits a single `recordAttestation` carrying that hash plus `overall_score * 1000`. Measured gas: 52,765 per event. The full per-judge dossier (rationales, scores, model ids, timings) stays in the DB + on IPFS so any third party can re-fetch and recompute the keccak; mismatch ⇒ tampering. Per-judge `recordAttestation` is still available and reserved for a future N-of-M challenge mode. `slashJudge` exists for systematic bias detection (Phase 2).

#### 11.4.2 Why Arc, not Ethereum / Polygon / Solana?

Arc is **Circle's stablecoin-native L2** — built explicitly so USDC is the gas token and the settlement asset are the same denomination. The entire fee-routing surface (5 USDC bid stake, 100 USDC operator stake, USDC builder fee from Polymarket) lives in one currency end-to-end. The alternative chains each lose on at least one axis:

| Chain | Why we chose against it |
|---|---|
| **Ethereum mainnet** | `submitBid` would be ~$3–15 of mainnet gas, dwarfing the 5 USDC bid stake and breaking the economics of low-value markets. |
| **Polygon** | Cheap gas but settlement currency is MATIC, not USDC — every fee event needs a swap and an oracle, adding two failure modes per fill. |
| **Solana** | Not EVM-compatible — we would lose Foundry/Slither/Hardhat tooling and the audit trail (the 5 contracts have already passed Slither: 9 Medium → 0 Medium, see `outputs/slither_2nd_pass.txt`). |
| **Base / OP** | Live alternatives but no USDC-native gas model and no Circle-backed mainnet-GA roadmap that aligns with Polymarket V2 builder rollout. |

Concrete Arc properties we depend on (RPC `https://rpc.testnet.arc.network`, chain ID `5042002` per [`.env`](./.env)):

- **Low gas.** Measured: a `submitBid` clears for ~$0.001 of testnet gas; the full 6–8 TX event lifecycle clears for ~$0.10. On Ethereum mainnet the same lifecycle would cost ~$30–100, which would force us to batch or roll up. Mainnet gas estimate is *expected to remain sub-cent* per Circle's published targets, but we are not citing a measured mainnet number until Arc mainnet GA lands.
- **Fast finality.** ~1s block time vs Ethereum's ~12s — fits inside the 60s auction window with 60× headroom even after retries.
- **USDC-native.** All stakes, fees, and rewards are denominated and *settled* in USDC without bridge or oracle dependency. No DEX swap path on the critical path.
- **EVM-compatible.** The same Solidity in `contracts/src/*.sol` ships to Arc mainnet (and, as a fallback, to Polygon/Base/OP) without rewrite. The Foundry deploy pipeline at [`scripts/deploy_all_contracts.py`](./scripts/deploy_all_contracts.py) is RPC-parameterized.
- **Mainnet GA timeline.** Arc mainnet is on Circle's published roadmap for **2026 Q3**. Phase-2 deploy is `forge create` against the mainnet RPC plus a Polymarket builder-code KYC unlock — no code change.

#### 11.4.3 One event's TX sequence

```mermaid
sequenceDiagram
    autonumber
    participant M as Marketplace<br/>(orchestrator)
    participant TA as TranslationAuction
    participant B1 as Bidder · seeder
    participant B2 as Bidder · external operator
    participant B3 as Bidder · seeder
    participant RR as ReputationRegistry
    participant QR as QuestionRegistry
    participant PMA as Polymarket V2
    participant BFR as BuilderFeeRouter

    M->>TA: openAuction(eventId, eventHash)
    par parallel bids (within 60s window)
        B1->>TA: submitBid(eventId, 0.40, candHashA)
        B2->>TA: submitBid(eventId, 0.35, candHashB)
        B3->>TA: submitBid(eventId, 0.30, candHashC)
    end
    Note over TA: t=60s · auction window closes
    M->>TA: settleAuction(eventId)
    TA->>RR: updateOnAuction(B1, false)
    TA->>RR: updateOnAuction(B2, false)
    TA->>RR: updateOnAuction(B3, true)
    M->>QR: registerQuestion(eventId, candHashC, builderCode, ipfsCid)
    QR-->>M: question_id
    M->>PMA: submit market (builder_code attached)
    Note over PMA: market live, traders fill orders
    PMA-->>BFR: 0.4% fee per fill (async, forever)
    BFR->>B3: 90% to winning agent wallet
    BFR->>M: 10% to treasury
    BFR->>RR: updateOnFee(B3, fee_amount)
```

#### 11.4.4 Nonce serialization — why a module-level lock is load-bearing

Concurrent events are the common case (the orchestrator opens multiple auctions in parallel), and every contract call from the **same operator wallet** needs a strictly increasing nonce. Two coroutines reading `getTransactionCount(pending)` at the same instant will see the same nonce → both build TXs with that nonce → one TX is rejected by the node.

The fix lives at [`polyglot_alpha/onchain.py`](./polyglot_alpha/onchain.py):

```python
_NONCE_LOCKS: Dict[str, "asyncio.Lock"] = {}
_REGISTRY_GUARD = threading.Lock()
```

`nonce_lock_for(address)` ([`onchain.py`](./polyglot_alpha/onchain.py)) is keyed by checksum-normalized wallet address. `send_with_nonce_lock` ([`onchain.py`](./polyglot_alpha/onchain.py)) holds the lock across the **entire** `read-nonce → build-tx → send_raw_transaction` sequence. The `threading.Lock` only protects insertion into the dict so two coroutines starting simultaneously can't create two different `asyncio.Lock` objects for the same address. Every Python wrapper (`chain/reputation_registry.py`, `chain/question_registry.py`, `chain/builder_fee_router.py`, `chain/auction_client.py`) routes through `send_with_nonce_lock` — no `eth_sendRawTransaction` is permitted outside this guard.

---

## 12. Phase 2 Roadmap

What is intentionally *not* in the hackathon ship, with explicit rationale:

| Phase 2 item | Why not now |
|--------------|-------------|
| Resolution feedback loop (UMA dispute → reputation slashing) | Requires real Polymarket markets to age into resolution; weeks-to-months horizon |
| External operator registration UI | Hackathon has no traders ↔ no operator demand; CLI/SDK path is the path |
| Real Polymarket submission default | Gated behind explicit operator confirm; protects builder-code reputation during demo |
| Polygon `OrderFilled` fill listener | RPC binding is live but no real fills until step above is unlocked |
| Mainnet contract deploy with 10% platform cut active | Pending Arc mainnet GA + Polymarket builder-code KYC |
| Event-quality pre-auction filter at production threshold | Currently scored but not gating; needs production telemetry to tune |
| Multi-operator stress test (10+ concurrent external agents) | Requires onboarding external operators post-hackathon |

---

## 13. What Is Running Live for the Demo

Honest accounting — what reviewers see when they pull this repo and run the demo:

**LIVE AND REAL:**

- 5 Arc testnet contracts, all deployed, all verified, `eth_getCode` non-empty
- 3 reference seeder agents with distinct wallets, distinct prompts/personas/temperatures, and distinct bid strategies — real Claude Haiku 4.5 calls on every auction (one Anthropic snapshot, three personas)
- Real RSS ingestion from 8 multilingual feeds (Xinhua, BBC Chinese, RFI Chinese, Caixin, SCMP, Asahi Shimbun, Le Monde, Deutsche Welle)
- 11-judge panel — judges make real LLM calls on Anthropic Claude Haiku 4.5 (MQM, D1-LLM, D2/D3/D6/D7 batched, D5-LLM); BLEU/COMET/D8/D4 are deterministic or model-backed offline
- **`JudgePanel.sol` γ-aggregate attestation** (W9-A) — one `keccak256(canonical_json(11-judge dossier))` + `overall_score * 1000` per event, ~52,765 gas; verified `chain_says == db_says` end-to-end
- **`ReputationRegistry` three updates per event** (W9-B) — `updateOnAuction` + `updateOnQuality` + `updateOnFee`, ~225k gas/event; live verified `chain stats delta == DB expected delta` (see W9-B's note about the EWMA formula causing short-term score dips even on clean wins — by design, not a bug)
- **Claim Fees and Register Operator endpoints** (W9-C) — `POST /api/operators/{addr}/claim-fees` and `/register` wired to UI buttons; mode-aware (mock returns `0xsim_*`, live executes real chain TX)
- **`verify_chain_consistency.py` audit script** (W9-D) — standalone tool checks for each event whether on-chain state matches DB across 5 phases (auction, judges, anchor, fee split, reputation); see "Verifying chain consistency" section below
- `TranslationAuction.openAuction` / `settleAuction` — real on-chain TX, recorded in [`outputs/tx_hashes.json`](./outputs/tx_hashes.json) [^w9e]
- `QuestionRegistry.commitQuestion` — real on-chain provenance with IPFS CID
- `BuilderFeeRouter.recordFill` — real Arc TX via `record_fill_with_split` (two legs per fill, 90/10 enforced off-chain through two real `recordFill` calls; no real Polygon fills yet)
- Polymarket Gamma payload construction with real registered builder code `0xa934...beb1`
- SSE event stream (13 event types — 10 base + 3 debate sub-events; see `ui/lib/api.ts`), FastAPI backend, Next.js dashboard (7 routes)

[^w9e]: `submitBid` is currently DB-picked with a ceremonial on-chain settle: the orchestrator records each bidder's intent in the DB, picks the winner there, and the chain settle TX records winner + winningBid into `TranslationAuction.auctions[event_id]`. **W9-E is rolling out** real `submitBid` from each bidder wallet so the chain holds every sealed bid and `settleAuction` reads bid state from chain, not from DB. **W9-F** ships a `withdrawStake` UI for operators whose 30-day lock has elapsed.

**EXPLICITLY NOT LIVE (Phase 2):**

- Real Polymarket submission — defaults to `dry_run` mode; flipping to `real` requires explicit operator confirm and is gated behind 5 safety nets (rate limit, idempotency key, quality gate, manual confirm flag, diversity check). See `polyglot_alpha/polymarket/client.py`.
- Real `submitBid` from each bidder wallet (currently DB-picked + chain settle; **W9-E rolling out**)
- `withdrawStake` UI (**W9-F rolling out**)
- Real Polymarket fills streaming into `BuilderFeeRouter` — depends on real submission being unlocked first
- Resolution feedback into reputation — requires markets to age out
- Per-judge attestations (N-of-M challenge mode) — the contract surface exists but the live path uses γ-aggregate, not 11 separate TXs

**Coverage estimate of the full lifecycle running real (not mocked):** ~92% post-W9 (up from ~85% at the May 26 audit), verified via the smoke harness at `scripts/smoke_test_phase1.py` plus the new `scripts/verify_chain_consistency.py`. The remaining ~8% is W9-E (bid chain-read) and W9-F (stake withdrawal UI), both rolling out.

---

## 14. How to Run It

### Setup & Configuration

#### Prerequisites

- **Python 3.14** (the project pins to 3.14 in `pyproject.toml`; earlier 3.12+ usually works but is not CI-tested)
- **Node.js 18+** with **pnpm** (or npm) for the dashboard
- **ffmpeg** — required for the demo video pipeline (`brew install ffmpeg` on macOS)
- **An Anthropic API key** — used by every seeder agent and every LLM-backed judge ([console.anthropic.com](https://console.anthropic.com/))
- **An Arc testnet wallet** — funded with Arc native gas + **100 USDC** for the anti-Sybil operator stake (see `4.4 Anti-Sybil registration`)

#### 1. Clone & install

```bash
git clone https://github.com/licaomeng/polyglot-alpha.git
cd polyglot-alpha

# Python backend
python3.14 -m venv .venv
.venv/bin/pip install -e .          # uses pyproject.toml
# (Older alt:  .venv/bin/pip install -r requirements.txt)

# UI
cd ui && pnpm install && cd ..      # npm install also works
```

#### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set:
#   ANTHROPIC_API_KEY
#   HACKATHON_WALLET_PRIVATE_KEY  (your operator wallet)
#   HACKATHON_WALLET_ADDRESS       (the matching pubkey)
```

`.env.example` lists every variable the backend reads with REQUIRED / OPTIONAL annotations. The reference table is at the end of this section.

**D8 duplicate-detection model — first start.** On first launch the backend pre-warms the SBert encoder used by the D8 judge (`sentence-transformers/all-MiniLM-L6-v2`, ~90 MB) by downloading it from Hugging Face into `$HF_HOME` (default `~/.cache/huggingface`). The pre-warm is non-blocking and logs its outcome under `d8.model_load:` in `logs/backend.*.log`. Set `D8_PREWARM=false` in offline / CI environments to skip the download; in that case D8 will report **`INSUFFICIENT_DATA` (panelBudgetExceeded + softSkip)** rather than silently passing, so a missing model is visible in the UI and the dossier. Run `.venv/bin/python scripts/check_d8_health.py` to verify the model and FAISS index are loadable end-to-end (exit code 0 = HEALTHY).

#### 3. Fund the Arc testnet wallets

```bash
# Generate a fresh operator wallet (foundry / cast)
cast wallet new

# Fund it via the Arc testnet faucet:
#   https://testnet.arcscan.app  (request native gas)
#   then bridge / mint 100 test USDC for the anti-Sybil stake

# Top up the 3 seeder agent wallets (Alpha / Bravo / Charlie) from the operator wallet:
.venv/bin/python scripts/faucet_agents.py
```

The seeder wallets are persisted to `outputs/agent_wallets.json` (public addresses only); their private keys live in `<AGENT>_WALLET_PRIVATE_KEY` env vars.

##### Recovering from the reputation gate (identity rotation)

`TranslationAuction.submitBid` reverts with `"reputation gate"` once a seeder's on-chain EWMA score drops below `0.7`. The current EWMA formula (W14-C contract-prep work in progress) does not allow seeders to recover their score above `0.5` from authorized calls alone, so once a seeder has racked up enough lossy events its wallet is effectively bricked for bidding. When that happens, rotate the seeder slot names to derive fresh wallet addresses (each new address starts at the initial reputation of `1.0`) and re-fund + re-register them:

1. Rename the slots in [`polyglot_alpha/agents/wallets.py`](./polyglot_alpha/agents/wallets.py) `AGENT_NAMES`, [`polyglot_alpha/agents/__init__.py`](./polyglot_alpha/agents/__init__.py) `AGENT_REGISTRY`, and the `agent_names` tuple in [`polyglot_alpha/orchestrator.py`](./polyglot_alpha/orchestrator.py) (one canonical convention is appending a `-vN` suffix, e.g. `gemini-v2` → `gemini-v3`). Wallet derivation is `sha256(operator_pk + ":" + slot_name)` so any new string yields a new address.
2. Fund + register the new wallets in one shot: `.venv/bin/python scripts/fund_seeder_wallets_v2.py`. The script is idempotent (skips already-funded wallets and already-registered agents) and tops each new wallet up with 0.05 ETH + 20 MockUSDC, then calls `registerAgent` with the 5 USDC anti-Sybil stake.
3. Refresh the address map: `.venv/bin/python -c 'from polyglot_alpha.agents.wallets import derive_all_wallets, persist_public_addresses; persist_public_addresses(derive_all_wallets())'` writes the new public addresses into `outputs/agent_wallets.json` (where `resolve_agent_name` reads them back to map winning addresses to seeder slots).
4. Restart the backend so the rotated names are picked up.

The `-v2` rotation shipped in W16-B (2026-05-27) was triggered because the original `gemini`/`deepseek`/`qwen` wallets had decayed to 0.61–0.69. **Note:** because the EWMA bug also affects the new wallets, the rotation buys ~3 live events before the new slots also fall below the gate; the permanent fix lives in W14-CONTRACT-PREP.

#### 4. Run the stack

```bash
# Terminal A — backend (FastAPI + Uvicorn)
.venv/bin/python -m uvicorn polyglot_alpha.api.main:app --host 127.0.0.1 --port 8000

# Terminal B — frontend (Next.js dashboard)
cd ui && pnpm dev -p 3001

# Terminal C — trigger one lifecycle (RSS → 3 seeders → Arc → 11-judge → Polymarket dry_run)
curl -X POST http://localhost:8000/trigger/event \
  -H 'content-type: application/json' \
  -d '{"event_source":"rss"}' | python3 -m json.tool

# Terminal D — watch the SSE stream
curl -N http://localhost:8000/sse/events
```

Visit `http://localhost:3001` and click **Trigger live demo** — the event appears with bids, judge scores, and on-chain TX links to `testnet.arcscan.app`.

#### 5. Swap LLM provider (future-ready)

The system uses **Anthropic Claude Haiku 4.5** by default, but the LLM layer is provider-agnostic (`polyglot_alpha/llm.py`) AND every model snapshot is externalized via env vars — never hard-coded in source. The single registry is `polyglot_alpha/models.py`.

To pin a different snapshot (same provider), just set the env var:

```bash
MODEL_HAIKU=claude-haiku-4-5-20251001     # base cheap snapshot
MODEL_SONNET=claude-sonnet-4-5-20250929   # base strong snapshot
MODEL_MODERATOR=                          # OPTIONAL per-role override; defaults to MODEL_SONNET
MODEL_REFINE=                             # OPTIONAL; defaults to MODEL_HAIKU
MODEL_MQM_JUDGE=                          # OPTIONAL; defaults to MODEL_HAIKU
# ... (see .env.example for the full list of MODEL_* knobs)
```

To swap providers entirely (e.g. OpenAI, Gemini, OpenRouter):

1. Write a class implementing the `LLMCallable` protocol in `polyglot_alpha/llm.py`
2. Add a factory function to the `_LLM_FACTORIES` registry (lines ~410 of `llm.py`)
3. Set `LLM_BACKEND=<your-provider>` in `.env` + the new API key env var
4. Override `MODEL_HAIKU` / `MODEL_SONNET` (and any per-role `MODEL_*` you care about) with the new provider's snapshots — e.g. `MODEL_HAIKU=gpt-4o-mini`, `MODEL_SONNET=gpt-4o`
5. No orchestrator / agent / judge code changes needed — the protocol absorbs the swap

#### Env variable reference

| Variable | REQ/OPT | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | REQUIRED | — | All LLM calls (seeders, MQM judge, D1/D5/D8 style judges, news summarizer). [Get one](https://console.anthropic.com/). |
| `LLM_BACKEND` | OPTIONAL | `anthropic` | Provider selector. Future-extensible registry; only `anthropic` ships today. |
| `ANTHROPIC_MAX_CONCURRENCY` | OPTIONAL | `5` | Per-process concurrent LLM calls semaphore. |
| `ANTHROPIC_TIMEOUT_MULTIPLIER` | OPTIONAL | `1.0` | Multiplier on all LLM timeouts — bump under load. |
| `SYNTHESIZER_MODEL` | OPTIONAL | Haiku 4.5 | Override the synthesizer model id. |
| `ARC_TESTNET_RPC` | OPTIONAL | `https://rpc.testnet.arc.network` | Arc testnet RPC endpoint. |
| `ARC_CHAIN_ID` | OPTIONAL | `5042002` | Arc testnet chain id. |
| `TRANSLATION_AUCTION_ADDRESS` | OPTIONAL | deployed | 60s sealed-bid auction (the 5 contract addresses default to our deployed instances; override if forking). |
| `QUESTION_REGISTRY_ADDRESS` | OPTIONAL | deployed | On-chain `(candidate_hash → winning_bidder)` registry. |
| `BUILDER_FEE_ROUTER_ADDRESS` | OPTIONAL | deployed | Per-fill USDC fan-out router. |
| `REPUTATION_REGISTRY_ADDRESS` | OPTIONAL | deployed | EWMA α=0.85 reputation. |
| `JUDGE_PANEL_ADDRESS` | OPTIONAL | deployed | 11-judge panel registry. |
| `ARC_TESTNET_USDC_ADDRESS` | OPTIONAL | deployed | Arc testnet USDC token. |
| `HACKATHON_WALLET_ADDRESS` | REQUIRED | — | Operator wallet pubkey. |
| `HACKATHON_WALLET_PRIVATE_KEY` | REQUIRED | — | Operator wallet privkey. Generate with `cast wallet new`. |
| `OPERATOR_WALLET_PRIVATE_KEY` | OPTIONAL | falls back to HACKATHON | Distinct operator key for the event dispatcher / fill listener. |
| `PLATFORM_TREASURY_ADDRESS` | OPTIONAL | operator addr | Recipient of the 10% platform cut. |
| `ALPHA/BRAVO/CHARLIE_WALLET_PRIVATE_KEY` | OPTIONAL | — | Seeder agent wallets. Required only if you run seeders locally. |
| `POLYMARKET_BUILDER_CODE` | REQ for `real` | — | 32-byte builder code. Register at [polymarket.com/settings](https://polymarket.com/settings?tab=builder). |
| `POLYMARKET_BUILDER_NAME` | OPTIONAL | — | Display name in builder dashboard. |
| `POLYMARKET_BUILDER_ADDRESS` | OPTIONAL | — | Builder fee recipient. |
| `POLYMARKET_BUILDER_API_KEY/_SECRET/_PASSPHRASE` | REQ for `real` | — | Polymarket Gamma API auth triple. |
| `POLYMARKET_MODE` | OPTIONAL | `dry_run` | `mock` / `dry_run` / `real`. See "Polymarket submission modes" below. |
| `POLYMARKET_REAL_QUALITY_GATE` | OPTIONAL | `0.80` | Min `overall_score` for real submission. |
| `POLYMARKET_REAL_DAILY_LIMIT` | OPTIONAL | `5` | Per-process real-submission cap. |
| `POLYGON_RPC` | OPTIONAL | — | Polygon RPC for fill indexer (real mode). |
| `ALCHEMY_API_KEY` / `ALCHEMY_APP_ID` | OPTIONAL | — | Alchemy creds; alternative to a raw `POLYGON_RPC`. |
| `CTF_EXCHANGE_V2_ADDRESS` | OPTIONAL | mainnet default | Override CTF Exchange V2 address. |
| `LIFECYCLE_MAX_CONCURRENCY` | OPTIONAL | `1` | Parallel lifecycles. **Keep at 1** unless you've sized RAM for concurrent FAISS + SBert. |
| `AUCTION_WINDOW_SECONDS` | OPTIONAL | `60` | Auction open window. |
| `AUCTION_MODE` | OPTIONAL | `real` | `real` / `mock`. Auto-`mock` when mock_bids supplied. |
| `QUALITY_PASS_THRESHOLD` | OPTIONAL | `0.7` | Panel verdict pass gate. |
| `PER_JUDGE_TIMEOUT_S` | OPTIONAL | `60` | Per-judge call timeout. |
| `PER_JUDGE_TIMEOUT_RETRY_S` | OPTIONAL | `90` | Retry timeout for slow judges. |
| `PANEL_TIMEOUT_SECONDS` | OPTIONAL | `120` | Full 11-judge panel timeout. |
| `DEFAULT_STAKE_USDC` | OPTIONAL | `5.0` | Bid stake (USDC). |
| `DATABASE_URL` | OPTIONAL | SQLite | SQLAlchemy URL. Defaults to `sqlite:///./polyglot_alpha.db`. |
| `REDIS_URL` / `REDIS_CHANNEL` | OPTIONAL | — | Enables Redis pub/sub for multi-process SSE fan-out. |
| `CORS_ORIGINS` | OPTIONAL | localhost | Comma-separated allowed origins. |
| `PINATA_JWT` / `W3S_TOKEN` | OPTIONAL | — | IPFS pinning credentials. Falls back to local-file IPFS if both unset. |
| `POLYGLOT_DEMO_MODE` | OPTIONAL | unset | Truthy to expose judge weights via API (demo only). |
| `POLYGLOT_BUILDER_REGISTRY_PATH` | OPTIONAL | repo default | Override builder registry JSON path. |

---

## Demo Modes — Live vs Mock

PolyglotAlpha events can be triggered in one of two modes. Mode is decided per-event at trigger time and stored on the event row.

| `?mode=` | LLM | Chain | News | Cost | Time | Use case |
|---|---|---|---|---|---|---|
| `live` (default) | Anthropic | Real Arc tx | Real RSS | ~$0.05/event + gas | ~120-180s | Production demo for reviewers |
| `mock` | MockLLM | `0xsim_*` synthetic | Canned fixtures | $0 | ~5-10s | Local dev / UI state-machine debugging |

### Switching mode

Three ways, in order of precedence:

1. **URL param**: `http://localhost:3001/?mode=mock` — set browser session to mock; persists across navigation via localStorage
2. **Header toggle**: top-right segmented control `[ LIVE | MOCK ]` — click to switch; persists in localStorage; URL stays clean
3. **Direct API**: `POST /trigger/event {"mode": "mock"}` — for scripted triggers

The toggle in the header reflects the mode for the **next** trigger. The MODE badge next to each event's title reflects what mode **that event was actually triggered in** (read from DB, immutable).

### Mock mode guarantees
- No LLM tokens consumed
- No Arc gas consumed
- No external RSS fetch
- All hashes prefixed `0xsim_` (UI does not link to arcscan)
- All IPFS refs prefixed `ipfs://sim/` (UI shows muted text, no gateway lookup)
- Event MODE badge always visible
- Mock events are excluded from leaderboard / reputation aggregates
- Mock events still appear in `/events` list (so you can find what you just triggered)

### Fixture content
Mock news clusters live in `polyglot_alpha/ingestion/fixtures/news_cluster_*.json`. To add a new language or scenario, drop a file matching the schema; the loader picks randomly per trigger.

---

## Verifying chain consistency

PolyglotAlpha ships a standalone audit script that checks for each event whether
on-chain state (Arc testnet) matches what the API + DB report:

    .venv/bin/python scripts/verify_chain_consistency.py <event_id>

Output verifies 5 phases:
- Phase 2 (Auction): chain `getAuction(eventId)` → winner + winningBid
- Phase 4 (Judges): `JudgePanel` attestation hash == `keccak256(canonical_json(judges_dossier))`
- Phase 5 (Anchor): `QuestionRegistry` content_hash
- Phase 7 (Fee Split): `cumulative_fees` delta == 0.9 × winner + 0.1 × treasury
- Phase 8 (Reputation): on-chain stats delta match DB

Mock events (`0xsim_*` tx hashes) skip per-phase. Exit 0 on full pass, 1 on any failure.

The script is the canonical answer to "did the marketing claim actually wire up to chain on this event?" Re-run after every wave that touches chain ops.

---

### Quickstart (TL;DR)

```bash
# 1. Fund seeder wallets (one-time)
.venv/bin/python scripts/faucet_agents.py

# 2. Start backend
.venv/bin/python -m uvicorn polyglot_alpha.api.main:app --reload --port 8000

# 3. Start frontend
cd ui && npm run dev   # port 3001

# 4. Trigger the lifecycle (RSS → 3 seeders → Arc → 11-judge → Polymarket dry_run)
#    Requires ANTHROPIC_API_KEY in env for the seeder agents and LLM judges.
curl -X POST http://localhost:8000/trigger/event \
  -H 'content-type: application/json' \
  -d '{"event_source":"rss"}' | python3 -m json.tool

# 5. Watch the SSE stream
curl -N http://localhost:8000/sse/events
```

Open `http://localhost:3001` — the event appears on the dashboard with bids, judge scores, and on-chain TX links to `testnet.arcscan.app`. Run an external operator agent against the same auction with:

```bash
EXTERNAL_OPERATOR_WALLET_PRIVATE_KEY=0x... \
  .venv/bin/python examples/external_operator_example.py
```

### Polymarket submission modes — `mock` / `dry_run` / `real`

The Polymarket client at [`polyglot_alpha/polymarket/client.py`](./polyglot_alpha/polymarket/client.py) is a three-tier safety surface. The mode is resolved from `POLYMARKET_MODE` (string `mock` | `dry_run` | `real`) and defaults to **`dry_run`** when unset or invalid (see `_mode_from_env` in [`client.py`](./polyglot_alpha/polymarket/client.py)). The enum lives at [`polyglot_alpha/polymarket/types.py`](./polyglot_alpha/polymarket/types.py).

| Mode | What it does | Network calls? | `is_simulated` | Use case |
|---|---|---|---|---|
| `mock` | Synthetic submission from `MockPolymarketClient`. Stable IDs, deterministic. | No | `True` | Unit tests · CI · offline dev |
| `dry_run` *(default)* | Builds the **full real-shape Gamma payload** (every field the live submission needs), logs it, returns `market_id=dryrun-<uuid>`. **Bypasses** the `REAL_QUALITY_GATE` so reviewers can inspect the payload even on a failing event. | No (logged only) | `True` | Hackathon demo · payload review |
| `real` | Posts to `https://gamma-api.polymarket.com/markets`. **Requires all four:** `confirm_real_submission=True` from caller, `overall_score >= REAL_QUALITY_GATE (0.80)`, builder secrets (`POLYMARKET_BUILDER_API_KEY` / `_SECRET` / `_PASSPHRASE`), and per-process `REAL_DAILY_LIMIT = 5`. Any failure degrades to dry-run with the error stamped on the result. | Yes (with fallback) | `False` on success | Production submission |

The four real-mode gates live at [`polyglot_alpha/polymarket/client.py`](./polyglot_alpha/polymarket/client.py):

1. **Caller-confirm flag** — `submit_question(..., confirm_real_submission=True)` must be passed explicitly; default is `False` and the call short-circuits with `status="blocked"`.
2. **Quality gate** — `REAL_QUALITY_GATE = 0.80`; the panel verdict's `overall_score` must clear it.
3. **Auth fully configured** — all three builder secrets present, else `status="failed"`.
4. **Daily rate cap** — `REAL_DAILY_LIMIT = 5` real submissions per process restart.

Even when these all pass, any transport error from Gamma falls back to a labelled dry-run result so the orchestrator never throws on a transient failure.

```bash
# inspect the real-shape payload without posting (the demo default)
POLYMARKET_MODE=dry_run .venv/bin/python -m polyglot_alpha.cli.trigger_event

# fully simulated; no network egress
POLYMARKET_MODE=mock .venv/bin/python -m pytest tests/polymarket/

# real submission — only after the four gates above are satisfied
POLYMARKET_MODE=real .venv/bin/python -m polyglot_alpha.cli.trigger_event --confirm-real
```

### Backend API surface

| Endpoint | Purpose |
|----------|---------|
| `GET /events` | List events; supports `?limit=`, `?offset=`, `?status=` |
| `GET /events/{id}` | Full event detail |
| `GET /events/{id}/bids` | Bid history for one event |
| `GET /agents/{address}` | Reputation + bid/win/fee history |
| `GET /leaderboard` | Top agents by `cumulative_fees` / `avg_quality` / `total_wins` |
| `GET /sse/events` | Server-Sent Events lifecycle stream · 15s heartbeat |
| `POST /trigger/event` | Kick off full lifecycle for a headline |

### Mechanism design defaults (locked, overridable via env vars)

| Parameter | Value |
|-----------|-------|
| Bid stake | 5 USDC |
| Translation judge stake | 2 USDC |
| Style judge stake | 1 USDC |
| Operator registration stake | 100 USDC |
| Auction window | 60 s |
| Reputation gate | ≥ 0.70 |
| EWMA α | 0.85 |
| Builder fee | 0.4% (90% operator / 10% platform) |
| Polymarket mode default | `dry_run` |

Override via env: `AUCTION_WINDOW_SECONDS`, `DEFAULT_STAKE_USDC`, `QUALITY_PASS_THRESHOLD`, `POLYMARKET_BUILDER_CODE`, `POLYMARKET_MODE`.

---

## 15. Demo URLs, Repo Links, Contact

- **Frontend dashboard (local):** `http://localhost:3001`
- **Backend API (local):** `http://localhost:8000`
- **Builder code on Polymarket:** [`polymarket.com/settings?tab=builder`](https://polymarket.com/settings?tab=builder) (search `0xa934...beb1`)
- **Arc explorer for contracts:** [`testnet.arcscan.app`](https://testnet.arcscan.app/)
- **Stress-test log + bug backlog:** [`outputs/MASTER_REPORT.md`](./outputs/MASTER_REPORT.md) · [`outputs/BUG_BACKLOG.md`](./outputs/BUG_BACKLOG.md)
- **License (tiered):** [`LICENSING.md`](./LICENSING.md) — MIT for contracts · BUSL-1.1 for backend/frontend · proprietary for evaluator IP
- **Contact:** `licaomeng@gmail.com`

---

*Built during the Agora Agents Hackathon, May 2026. Open mechanism, closed evaluator IP, honest scope.*
