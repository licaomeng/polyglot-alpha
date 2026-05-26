# Honest Scope Statement

PolyglotAlpha v2 is **proof-of-mechanism, not proof-of-market**. This document says exactly what's real, what's mocked, and what's deliberately out of hackathon scope, so judges and downstream readers can calibrate. Cross-references thesis §5.30.

## Phase 1 ship progress (as of submission)

A pre-submission audit (§5.47) catalogued 31 mock/stub paths across the codebase. Five demo-readiness decisions were then locked (§5.48) to drive the "Phase 1 ship" — the goal is moving the real-coverage ratio from ~25–30% at audit time to ~80% by submission. Concrete deltas:

| Layer                       | Before Phase 1 (audit)        | After Phase 1 (ship state)                                  |
|-----------------------------|-------------------------------|-------------------------------------------------------------|
| RSS ingestion               | Fixture events                | Real multi-source RSS pull (Caixin / Xinhua / Nikkei / etc) |
| Auction transactions        | Mocked log lines              | Real Arc TXs against `TranslationAuction` (re-deployed)     |
| Agent bids                  | Hard-coded LLM responses      | Real LLM calls per seeder (3 personas, all on Anthropic Claude Haiku 4.5; prompts/temps/strategies differ — not the model) |
| Judge panel                 | Stub scores                   | Real 11-judge LLM evaluation with hard / soft gates         |
| On-chain anchor             | Mock TX hash                  | Real `QuestionRegistry.commitQuestion` on Arc               |
| Polymarket submission       | Always-mock                   | Real Gamma API call in `dry_run` by default; opt-in real    |
| Polymarket fill listener    | Not implemented               | Still mocked → **Phase 2** (post-submission)                |
| CCTP V2 Polygon → Arc bridge| Scaffold only                 | Production wiring **deferred** (needs Polygon RPC + Circle) |

The "Phase 1 in progress" framing is honest: at submission time the orchestrator + UI + contracts are real, and the production Polymarket fill loop is not. Anything still mocked is called out explicitly in the next section.

## What is real (ships in the demo)

### Real on Arc testnet (chain ID `5042002`)

Six contracts deployed and operational (all hardened with `ReentrancyGuard` in the post-audit redeploy), all transacted within the 2026-05-11 → 2026-05-25 hackathon window:

| Contract              | Address                                      | Verifiable at                              |
|-----------------------|----------------------------------------------|--------------------------------------------|
| TranslationAuction    | `0xE046Ea8478855A653bAdc9Fbd12ae4B8A429907a` | `https://testnet.arcscan.app`             |
| BuilderFeeRouter      | `0xcE7596d9b21333Eae441E912699514F6fBD150e5` | `https://testnet.arcscan.app`             |
| ReputationRegistry    | `0x00267FD2FFabDDB48bBF16e3a91C15DE260eF9F1` | `https://testnet.arcscan.app`             |
| JudgePanel            | `0x1eE7BADc48b52B36e086adb4a98E00cbff4efd9a` | `https://testnet.arcscan.app`             |
| QuestionRegistry      | `0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1` | `https://testnet.arcscan.app`             |
| MockUSDC              | `0x477fC4C3DcC87C3Ceb13adc931F6bBeDAcCa391D` | `https://testnet.arcscan.app`             |

These accept real calls, store real events, and can be independently queried. Gas is paid in USDC (Arc-native), which satisfies the Agora submission floor "≥1 USDC-denominated transaction on Arc during the window."

### Real off-chain

- **Three reference seeder agents — same backbone, distinct personas.** Seeder Alpha (macro), Seeder Beta (geopolitics), Seeder Gamma (markets). All three run on Anthropic Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) via the Anthropic SDK direct; differentiation is by prompt, temperature, and bid-strategy heuristic. Each has its own wallet, its own deterministic bid strategy, and registers through the same public SDK that any external operator would use. Backbone homogeneity for the seeders is acceptable because the 11-judge panel is what enforces independence (heterogeneous judges: Haiku for MQM, sentence-transformers for D8, sacrebleu for BLEU, COMET QE). Legacy class names `GeminiAgent` / `DeepSeekAgent` / `QwenAgent` are re-exported from `polyglot_alpha.agents` so wallet derivation stays stable across the rename — they are not Gemini/DeepSeek/Qwen models.
- **Eleven independent judges.** Three translation judges (BLEU-MQM, COMET reference-free, LLM-MQM) and eight style-alignment judges (D1–D8). Each judge is a separate Python module under `polyglot_alpha/judges/` with its own LLM binding or rule-based logic.
- **5-layer translation pipeline.** Source Analysts → Debate → Synthesis → Risk Panel → Output. Implemented in `polyglot_alpha/translators.py`, `synthesizer.py`, `analysts.py`.
- **Polymarket corpus (Component +11).** 5K+ questions scraped from gamma-api, embedded with `sentence-transformers/all-MiniLM-L6-v2`, FAISS-indexed, distilled into `style_guide.md` + `few_shots.json` + `patterns_report.md`. Rebuild scripts are in the repo.
- **Multi-source RSS ingestion + cross-reference.** Live aggregator under `polyglot_alpha/ingestion/`.
- **FastAPI orchestrator.** Async state machine wiring components 1→10, with SSE for live UI updates. Endpoints documented in repo `README.md` "Backend API" section.
- **Next.js 14 UI with 7 routes.** Workflow DAG, events list, per-event 7-phase timeline, per-agent profile, leaderboard, history, about. Real-time SSE updates from FastAPI.

## What is mocked (out of demo-day scope)

### Mocked agent ecosystem

The 4 translator agents are **first-party mocks with real LLM bindings** — they're genuinely different LLMs running genuinely different prompts, but they are *all operated by the submitter*. There is no real third-party bidder ecosystem on PolyglotAlpha today. Bootstrapping that is a 6-12 month problem (recruit operators, ship a bidder SDK, run a qualifying track to issue reputation). Out of hackathon scope. Roadmap item.

### Mocked Polymarket production fills during demo

The Polymarket Gamma API submission path is wired and operational: builder name `polyglot-alpha`, builder address `0x3d423b073a7bb0f79d2f20d65593db09aa80d8bf`, builder code `0xa93402f8ae6ac4a7b1d863d80145daa74f89cb4834fc0d86b36c1e4e1d6fbeb1`, 0.4% maker fee effective 2026-05-29. By default the demo runs the submission in `dry_run` mode to avoid spamming Polymarket curators with throwaway dev traffic. A "Submit Real" toggle exists in the UI for opt-in production submission, gated by the §5.43 three-tier safety net (max 5/day rate limit + content-hash idempotency + 11-judge quality threshold ≥ 0.80).

### Mocked Polymarket fill listener

The Polygon `OrderFilled` indexer that triggers builder-fee streaming via CCTP V2 is **Phase 2** work, not in this submission. It's scaffolded against the V2 spec but not wired to a live Polygon RPC. When real fills land on submitted markets they will accrue automatically once Phase 2 ships.

### Deferred — CCTP V2 production bridge

`BuilderFeeRouter.sol` is deployed on Arc and accepts fee-routing calls. The off-chain CCTP V2 burn-mint flow Polygon → Arc is **deferred** for production wiring — it needs a stable Polygon RPC subscription, Circle attestation polling, and a Polygon-side hot wallet, none of which are appropriate to stand up in a hackathon week. Tested against a synthetic `OrderFilled` event stream; production deferred per §5.30 item 2.

## Closed evaluator IP boundary

What stays private — even in an open-source MIT repo — by deliberate design (thesis §5.27):

- The 11-judge specific weighting function across the 3 + 8 panel
- The Polymarket corpus snapshot (`*.parquet`, `*.faiss` are gitignored; only summary statistics are public)
- The exact threshold values: D1 ≥ 0.75, D5 ≥ 85, D8 ≥ 0.08, MQM ≥ 80 (these are *example* values in the public docs; the live values rotate)
- The few-shot exemplar library driving D3/D4/D5/D7 LLM-judge prompts
- The anti-pattern detection algorithms (regex / entropy / kNN code) per D1–D8 dimension
- Negative training data (hand-curated bad-question examples)

What is published openly:

- All five smart contracts under MIT (`contracts/src/*.sol`)
- The FastAPI submission API spec
- The orchestrator state machine
- The agent SDK scaffolding
- The reputation update rule (`0.7 × MQM/100 + 0.3 × revenue_percentile`, EWMA α = 0.85)
- The D1–D8 dimension *names* and 1-line definitions

The analogy is Moody's / S&P / FICO / Google / ETS: publish the rating-scale concept, keep the specific weights private. Without this boundary, every rational bidder reverse-engineers the rubric, outputs converge, and the auction collapses into a Bertrand price war (the convergence paradox, thesis §5.27).

## Why this scope is the right one

A working *mechanism* with *real on-chain attestation* and *real multi-LLM agent competition* — even with mocked bidders and mocked Polymarket fills — is the highest-leverage artifact a solo builder can ship in 14 days on a zero-dollar budget. It's the artifact that lets a downstream funder, a regulator, or Polymarket itself reason about whether the mechanism is worth productionizing. Everything I *don't* ship is downstream of "the mechanism actually works in code" — which is exactly what this submission proves.

**One-line summary:** proof-of-mechanism, not proof-of-market. The mechanism runs end-to-end on Arc testnet; productionizing it is the next person's problem.

## Anti-overselling commitments

- The Loom video does not claim Polymarket fills are live; it explicitly flags the `SUBMITTED (mock)` badge in Phase 6 (`submission/demo_script.md` block C, 1:54–2:00).
- The repo `README.md` "Honest scope" section is the same statement, abbreviated, so anyone landing on GitHub sees the boundary before they look for features.
- The UI surfaces "Live on Arc testnet" vs "Simulated fill" vs "Historical" badges on every phase card (thesis §5.31).
- This document is committed to the submission alongside the form, not buried in the thesis.
