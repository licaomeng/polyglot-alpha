# Architecture

Four Mermaid diagrams that render on GitHub. Together they cover the static component graph, the runtime phase lifecycle, the open/closed IP boundary, and the Phase 1 ship-state real data flow.

## 1. The 10+1 components

```mermaid
graph LR
    subgraph OFFCHAIN["Off-chain (Python + Next.js)"]
        C1["1. Event Watcher<br/><i>polyglot_alpha/ingestion/</i><br/>RSS pool + cross-ref"]
        C3["3. Reference Seeders x3<br/>Alpha / Beta / Gamma — Claude Haiku 4.5<br/><i>polyglot_alpha/agents/</i>"]
        C4["4. 5-Layer Pipeline<br/>Source Analysts -> Debate -> Synth -> Risk -> Output<br/><i>polyglot_alpha/translators.py</i>"]
        C5["5. 11-Judge Panel<br/>3 translation MQM + 8 style D1-D8<br/><i>polyglot_alpha/judges/</i>"]
        C7["7. Polymarket V2 Client<br/><i>polyglot_alpha/polymarket/</i>"]
        C10["10. UI Dashboard (7 routes)<br/>Next.js 14 + React Flow + Framer<br/><i>ui/app/</i>"]
        C11["+11. Polymarket Corpus<br/>5K questions, FAISS, style guide<br/><i>corpus/</i>"]
    end

    subgraph ONCHAIN["On-chain (Arc testnet, chain 5042002)"]
        C2["2. TranslationAuction.sol<br/>Sealed-bid, reputation-gated<br/>0xE046...907a"]
        C6["6. QuestionRegistry.sol<br/>Provenance + attestation<br/>0x9b7D...81B1"]
        C8["8. BuilderFeeRouter.sol<br/>Per-fill USDC fan-out<br/>0xcE75...50e5"]
        C9["9. ReputationRegistry.sol<br/>EWMA alpha=0.85<br/>0x0026...F9F1"]
        JP["JudgePanel.sol<br/>0x1eE7...fd9a"]
        USDC["MockUSDC<br/>0x477f...391D"]
    end

    POLY["Polymarket V2<br/>(external)"]

    C1 --> C2
    C2 --> C3
    C3 --> C4
    C4 --> C5
    C11 --> C5
    C5 --> JP
    C5 --> C6
    C6 --> C7
    C7 --> POLY
    POLY -.fills.-> C8
    C8 --> C9
    C8 --> C3
    C9 --> C2
    C10 -.reads.-> C1
    C10 -.reads.-> C2
    C10 -.reads.-> C6
    C10 -.reads.-> C9

    USDC -.escrow.-> C2
    USDC -.stake.-> JP
    USDC -.fee.-> C8

    style C2 fill:#1f2937,stroke:#10b981,color:#fff
    style C6 fill:#1f2937,stroke:#10b981,color:#fff
    style C8 fill:#1f2937,stroke:#10b981,color:#fff
    style C9 fill:#1f2937,stroke:#10b981,color:#fff
    style USDC fill:#1f2937,stroke:#10b981,color:#fff
    style JP fill:#1f2937,stroke:#10b981,color:#fff
    style POLY fill:#1f2937,stroke:#9ca3af,color:#fff
```

![Component graph (10+1 components)](diagrams/mmd_00.png)

Reading the graph
- Green outlined nodes are deployed Arc-testnet contracts (all six contracts deployed and hardened with `ReentrancyGuard` after the Phase 1 audit).
- Grey is external (Polymarket V2).
- Dashed edges are reads/observations; solid edges are writes.

## 2. Phase lifecycle (one event, end-to-end)

```mermaid
flowchart TD
    P0([Mandarin headline arrives])
    P1[Phase 1 - Event Ingestion<br/>multi-source RSS + cross-reference<br/>emit canonical event hash]
    P2[Phase 2 - USDC Auction<br/>60s sealed-bid window<br/>3 seeders + N external operators bid, reputation-gated<br/>highest-score qualified bid wins]
    P3[Phase 3 - Translation Pipeline<br/>winning agent runs 5-layer pipeline<br/>K=5 framing variants emitted]
    P4[Phase 4 - 11-Judge Panel<br/>3 translation MQM judges<br/>8 style judges D1-D8<br/>aggregate score + verdict]
    GATE{Pass?<br/>MQM>=80<br/>3 hard gates<br/>4/5 soft gates}
    P5[Phase 5 - On-chain Anchor<br/>QuestionRegistry.questionCommitted<br/>full attestation transcript]
    P6[Phase 6 - Polymarket Submission<br/>REST POST with builder code<br/>question listed]
    P7[Phase 7 - Fee Streaming<br/>OrderFilled indexer reads Polygon<br/>CCTP V2 -> Arc<br/>BuilderFeeRouter -> translator wallet]
    REP[ReputationRegistry update<br/>EWMA alpha=0.85<br/>winner reputation moves up or down]
    SLASH[Slash bid stake<br/>72h slashable window]

    P0 --> P1
    P1 --> P2
    P2 --> P3
    P3 --> P4
    P4 --> GATE
    GATE -- yes --> P5
    GATE -- no --> SLASH
    P5 --> P6
    P6 --> P7
    P7 --> REP
    SLASH --> REP

    style P0 fill:#0f172a,stroke:#64748b,color:#fff
    style GATE fill:#7c2d12,stroke:#fbbf24,color:#fff
    style SLASH fill:#7f1d1d,stroke:#ef4444,color:#fff
    style P7 fill:#064e3b,stroke:#10b981,color:#fff
    style REP fill:#1e3a8a,stroke:#60a5fa,color:#fff
```

![Phase lifecycle flowchart](diagrams/mmd_01.png)

Phase 4 hard gates: D1 (structural), D5 (resolution clarity), D8 (duplicate), aggregate MQM ≥ 80. Soft gates: ≥ 4 of 5 from {D2, D3, D4, D6, D7}. See repo `README.md` "Mechanism design defaults" and thesis §5.22.

## 3. Open / closed IP boundary

```mermaid
graph TB
    subgraph OPEN["OPEN - MIT licensed, public on GitHub"]
        direction LR
        O1["TranslationAuction.sol"]
        O2["QuestionRegistry.sol"]
        O3["BuilderFeeRouter.sol"]
        O4["ReputationRegistry.sol"]
        O5["JudgePanel.sol"]
        O6["FastAPI submission API"]
        O7["Orchestrator state machine"]
        O8["Agent SDK scaffolding"]
        O9["Reputation update rule<br/>0.7 x MQM/100 + 0.3 x rev_pct<br/>EWMA alpha=0.85"]
        O10["Auction mechanism spec<br/>sealed-bid, lowest qualified, gate>=0.70"]
        O11["D1-D8 dimension <i>names</i> + 1-line definitions"]
    end

    subgraph CLOSED["CLOSED - core IP, never published"]
        direction LR
        K1["11-judge specific weights"]
        K2["Polymarket corpus snapshot<br/>5K parquet + FAISS index"]
        K3["Style guide full text<br/>(only distilled patterns are public)"]
        K4["Threshold values<br/>D1>=0.75, D5>=85, D8>=0.08, MQM>=80<br/>+ periodic retune schedule"]
        K5["Few-shot exemplar library<br/>D3/D4/D5/D7 LLM-judge prompts"]
        K6["Anti-pattern detection algorithms<br/>per-dimension regex / entropy / kNN code"]
        K7["Negative training data<br/>hand-curated bad-question examples"]
    end

    SUBMIT["Translator Agent (any party)"]
    PROBE["Adversary probing the rubric"]

    SUBMIT -- "uses public API,<br/>can fork, register, stake, bid" --> OPEN
    PROBE -.blocked.-> CLOSED

    OPEN -- "Defenses against reverse-engineering<br/>(rate-limit / stake-slash on rejection /<br/>threshold rotation / corpus rotation /<br/>judge ensemble randomization)" --> CLOSED

    style OPEN fill:#064e3b,stroke:#10b981,color:#fff
    style CLOSED fill:#7f1d1d,stroke:#ef4444,color:#fff
    style SUBMIT fill:#1e3a8a,stroke:#60a5fa,color:#fff
    style PROBE fill:#3f3f46,stroke:#71717a,color:#fff
```

![Open / closed IP boundary](diagrams/mmd_02.png)

Pattern reference: Moody's / S&P / FICO / Google search / ETS — publish the rating-scale concept, keep the specific weights private. Full rationale in thesis §5.27 (information-disclosure paradox) and §5.28 (Hayek tacit-knowledge argument).

## 4. Phase 1 ship-state — real data flow (post-§5.48)

Sequence diagram of one "Trigger live demo" click, mapping the real on-chain and off-chain calls that fire during the 60-second wall-clock lifecycle. Compare to diagram 2 for the abstract phase model; this one shows actual service-to-service edges in the Phase 1 ship state.

```mermaid
sequenceDiagram
    participant U as User
    participant UI as Frontend (Next.js)
    participant O as Orchestrator
    participant R as RSS Aggregator
    participant A as 4 Agents
    participant CT as Arc Chain
    participant J as 11-Judge Panel
    participant P as Polymarket Gamma API

    U->>UI: Click "Trigger live demo"
    UI->>O: POST /trigger/event {event_source: "rss"}
    O->>R: Fetch latest non-English event
    R-->>O: ConfirmedEvent (Caixin/Xinhua)
    O->>CT: openAuction (real Arc TX)
    O->>A: 3 seeders evaluate_event (real Claude Haiku 4.5 calls)
    A-->>CT: submitBid x3 (real Arc TX)
    O->>CT: settleAuction → highest reputation-adjusted score
    O->>A: Winner runs 5-layer pipeline (real LLM)
    A-->>O: final_question
    O->>J: evaluate via 11-judge (real LLM)
    J-->>O: PASS verdict + scores
    O->>CT: commitQuestion (real Arc TX)
    O->>P: dry_run submission OR real if user toggles
    P-->>O: market_id (dryrun-{uuid} or real)
    O-->>UI: SSE event.finalized
    UI-->>U: Show full Timeline + Arc explorer links
```

![Phase 1 ship-state sequence diagram](diagrams/mmd_03.png)

What this diagram shows that diagram 2 does not
- The `event_source: "rss"` flag — Phase 1 ships with real RSS aggregation as the default path; mock-event injection is a debug-only fallback.
- Real Arc transactions on every `openAuction`, `submitBid`, `settleAuction`, and `commitQuestion` call — verifiable on `https://testnet.arcscan.app` against the addresses in the README.
- 11-judge panel runs real LLM calls (not stub scores) — `PASS` verdict requires aggregate MQM ≥ 80 plus the three hard gates per §5.22.
- Polymarket Gamma API submission defaults to `dry_run`; user must explicitly toggle "Submit Real" in the UI to flip to a production submission, gated by the §5.43 rate limit + idempotency + quality gate.
- SSE channel pushes phase transitions to the UI in real time — no polling; the 60-second wall-clock is observed, not simulated.

## 5. Quick-reference: file layout to component map

| Component         | Primary source path                                          |
|-------------------|--------------------------------------------------------------|
| 1. Event Watcher  | `polyglot_alpha/ingestion/`                                  |
| 2. Auction        | `contracts/src/TranslationAuction.sol`                       |
| 3. Agents         | `polyglot_alpha/agents/{gemini,deepseek,qwen}_agent.py` (3 files; classes aliased to `SeederAlpha`/`SeederBeta`/`SeederGamma`; all back on Claude Haiku 4.5) |
| 4. Pipeline       | `polyglot_alpha/translators.py`, `synthesizer.py`, `analysts.py` |
| 5. Judges         | `polyglot_alpha/judges/translation/`, `polyglot_alpha/judges/style_alignment/`, `polyglot_alpha/judges/panel.py` |
| 6. QuestionRegistry | `contracts/src/QuestionRegistry.sol`                       |
| 7. Polymarket client | `polyglot_alpha/polymarket/client.py`, `mock_client.py`   |
| 8. BuilderFeeRouter | `contracts/src/BuilderFeeRouter.sol`                       |
| 9. ReputationRegistry | `contracts/src/ReputationRegistry.sol`                   |
| 10. UI            | `ui/app/`                                                    |
| +11. Corpus       | `corpus/`, `polyglot_alpha/corpus/`                          |
| Orchestrator      | `polyglot_alpha/orchestrator.py`                             |
| API               | `polyglot_alpha/api/main.py`                                 |
