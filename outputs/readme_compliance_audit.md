# README §5 vs UI Compliance Audit (2026-05-26)

**Build under test:** PolyglotAlpha v2 — frontend `localhost:3001` (Next.js 15.5.18), backend `localhost:8000` (FastAPI). Live audit driven by Playwright MCP. Triggered event #81 from the home page; observed phases 1-7 end-to-end.

## Summary
- Pages visited: 7 / 7 (home, events list, event detail #81, leaderboard, agent profile, history, about)
- Compliance checks: 32 distinct README-spec items
- Match README spec: **9 / 32**
- Partial / shallow: **15 / 32**
- Missing: **8 / 32**

## Per-phase compliance table

| Phase | README § | What the spec promises | What UI actually shows | Match? |
|---|---|---|---|---|
| **1. Event Ingestion** | §5.4, §5.33 | source name/url, language, content_hash, ingestedAt timestamp, multi-source cross-ref | Source label (`xinhua`), headline, `ingested 8h ago` relative time. **No `content_hash`** rendered, **no multi-source list**, **no language tag** in card. API returns all of these. | Partial |
| **2. USDC Auction** | §5.6, §5.32, §5.34 | 4 bids, agent_address, bid_amount, stake_amount, candidate_hash, tx_hash, reputation, winner highlighted, settlement_tx_hash | 4 bid rows visible; columns Agent/Bid/Rep./Winner badge; lowest bid ($0.45 by `0xgemini_agent`) correctly highlighted as winner. **stake_amount, candidate_hash, settlement_tx_hash all hidden** despite API returning them. | Yes (core) / Partial (detail) |
| **3. Translation Pipeline** | §5.5 (5 layers L1-L5), §5.7 (worked example), §5.14 (debate trace), §5.21 (K=5 framing variants) | 5 layer cards: Source Analysts (4 analysts) → Bull/Bear Translator Debate → Synthesizer → Risk Panel → Output. Each layer with Pydantic-trace + debate transcript + K=5 framings + final question_json. | **TITLE + "Done" badge ONLY.** No 5 layers, no analyst trace, no debate, no framings, no final-question card. API has `final_question` JSON ready (full Polymarket-shaped object) but timeline card discards it. The graph node "STEP 05 Analyst Debate" is a label-only node. | **Missing** |
| **4. 11-Judge Panel** | §5.6, §5.22, §5.25 | 3 translation judges (BLEU + COMET + MQM-LLM, score 0-100) + 8 style judges D1-D8 with numeric scores, hard gates (D1 ≥0.75, D5 ≥85, D8 ≥0.08, MQM ≥80), soft gates ≥4/5, PASS/REVIEW/REJECT verdict, divergence diagnosis, Closed-IP 🔒 callout. | **TITLE + "Done" badge ONLY** on timeline. API returns: `translation_scores.{bleu,comet,mqm}` all **null** (not computed in mock), `style_alignment_passes` is 8 booleans **all true** (no numeric scores, no hard/soft gate breakdown, no divergence). Verdict (`PASS`) and overall_score (`0.65`) exist in API but unrendered. Closed-IP banner exists on /about but not on event page. | **Missing** in UI; mock backend also under-spec'd (no real BLEU/COMET/MQM values) |
| **5. On-chain Anchor** | §5.10 | TX hash clickable to Arc explorer, QuestionRegistry contract address, question_id from chain | **TITLE + "Done" badge ONLY.** API returns `question_id` = `0x4494c2ba9bbad3e9f5d6401219ad5ef8046692ca` and `builder_code` but neither rendered as clickable link. Auction phase does show a `tx_hash` field, but not exposed in the UI card. No Arc explorer link. No contract address display (`ArcExplorerEmbed` component in spec). | **Missing** |
| **6. Polymarket V2 Submission** | §5.9 | market_id, market_url clickable, is_simulated yellow badge, builder_code | **TITLE + "Done" badge ONLY.** API provides `market_id="mock-09baa32e8188"`, `market_url="https://polymarket.com/market/mock-09baa32e8188"`, `is_simulated=true`, `builder_code="POLYGLOT_ALPHA_BUILDER_V1"` — none rendered inline. (The top header does show a generic page-level "Mock" badge.) | **Missing** |
| **7. Streaming Revenue** | §5.15, §5.31 (BuilderFeeStream component) | Cumulative fee count, per-fill stream, USD amount, Recharts time-series | **TITLE + "Done" badge ONLY.** API returns `phase-7.details={streaming:true}` only — no fee events array, no $ amount, no chart. Spec promises `builder_fee_events` table with per-fill stream — backend `/events/{id}` does not surface it. | **Missing** |

## Workflow Overview (§5.31) — separate verdict
React Flow DAG renders **11 nodes** (matches §5.31 "10+1 components"). Edges + pan/zoom/fit controls present. Node statuses transition pending→completed correctly. ✓ Matches spec.

## Other pages
| Page | README § | UI status |
|---|---|---|
| `/` Home | §5.36 | Hero + 3 thesis cards (Cross-language alpha / Verifiable pipeline / Streaming fees) + WorkflowOverview + 3 featured events + Trigger button. ✓ Strong. |
| `/events` Events list | §5.36 | Renders; status filtering UI not deeply checked but page works. ✓ |
| `/leaderboard` | §5.36 | Sortable table (Rep / Revenue / Win rate), revenue distribution chart present. **Pollution:** 16+ test agents (`0xagent1..10`, `0xa`, `0xp`, `0xff`, `0xsame`, etc.) dilute the canonical 4 agents — `0xgemini_agent` ranks #3, not #1. Demo-cleanup needed. Partial. |
| `/agents/0xgemini_agent` | §5.36 | Reputation 0.65, Revenue $5.00, Win rate 45%, time-series dual-axis chart. **Missing:** explicit `wins` / `losses` / `totalBids` counts; **missing** quality-avg metric (spec promises `avg_quality`). Partial. |
| `/history` | §5.36 | Search box, status filter dropdown, CSV export button. ✓ Matches. |
| `/about` | §5.36 | 7-section mechanism walkthrough with Closed-IP banner. ✓ Matches §5.17/§5.27 themes. |

## SSE compliance (§5.32, §5.37)
**README promises 10 event types**: `event.created`, `auction.opened`, `bid.submitted`, `auction.settled`, `translation.completed`, `quality.verdict`, `onchain.committed`, `polymarket.submitted`, `builder_fee.accrued`, `event.finalized`.

**Observed:** Subscribing to `/sse/events` returns only one event on connect: `event: hello\ndata: {"subscribers": 3}`. Triggering a new event while listening for 8 s produced **0 named events**. The UI shows "sse connected" badge but the connection is decorative — no real-time phase-by-phase updates flow. Event detail page renders only after page refresh.

**Verdict on SSE:** 0/10 spec'd event types. README §5.37 promise of `useEventStream(eventId)` hook driving phase status transitions is unimplemented.

## Closed-IP indicator (§5.38)
README promises 🔒 Private callout on judge weights pages. Found on `/about` (generic banner). **Not found** on event detail page next to the 11-Judge phase, which is where spec says it belongs.

## Missing features (need to add)
1. **5-layer translation pipeline trace UI** (`PipelineLayerCard`, `DebateTrace`, `SynthesizerOutput` from §5.31 component tree) — currently zero of these render
2. **11-judge inline panel** (`TranslationJudges` BLEU/COMET/MQM + `StyleAlignmentJudges` D1-D8 with numeric scores + hard/soft gate breakdown + `ClosedIPCallout`)
3. **K=5 framing variants** display (§5.21) — backend never produces them, UI never shows them
4. **Real translation metrics** — backend mock returns `translation_scores.bleu/comet/mqm = null`; should compute even simulated values for demo realism
5. **Real-time SSE per-phase updates** — the 10 spec'd event types are unimplemented
6. **Arc explorer TX links** (§5.10) — `tx_hash` is in API for auction phase but never rendered as clickable link
7. **Builder fee stream chart** (`BuilderFeeStream` from §5.31) — no fee-event array surfaced
8. **Final question JSON card** — backend returns full Polymarket-shaped `final_question` object; UI discards it

## Partial features (need to expand)
1. **Phase timeline cards 3-7** — render only `title + Done` badge; need to render `details{}` payload which the API already returns
2. **Auction card** — has core data but hides stake_amount, candidate_hash, tx_hash
3. **Polymarket card** — needs is_simulated 🟡 yellow badge per §5.38 + clickable market_url
4. **Agent profile** — add explicit wins/losses/totalBids counts + avg quality score
5. **Leaderboard** — filter out test/seed agents (0xagent1..10, 0xa, 0xp, 0xff, 0xsame) so canonical 4 are top
6. **Closed-IP 🔒 callout** — place on event detail page above 11-Judge card (currently only on /about)
7. **Multi-source ingestion** (§5.4) — backend stores only 1 source per event; spec promises ≥2-source cross-reference
8. **MQM 9-category breakdown** — API has `mqm.errors[]` but always empty

## Suggestions to bring UI closer to README (ranked by impact)
1. **(highest)** Implement timeline-card `details` rendering for phases 3-7. The data is already in `phases[].details` — just render it. This is a pure-frontend change, ~1 file per phase card. Without this, the demo's flagship "show the multi-agent debate, show the indicator evaluation" pitch is undeliverable.
2. **Add real BLEU/COMET/MQM numeric values to mock mode** so the 11-judge panel can display the score table from §5.22. Even fake-but-plausible numbers (0.72 / 0.81 / 84) beat `null`.
3. **Wire up SSE phase-by-phase events** — even just `phase.update` per phase transition would make the UI feel live instead of "refresh to see Done".
4. **Render `final_question` JSON in Phase 3** — this is the most impressive single piece of output (full Polymarket-shaped question) and it's currently invisible.
5. **Show 8 D-dimension scores as bar chart in Phase 4** — booleans are uninspiring; even mock numeric scores 0.0-1.0 will demo well.
6. **Make tx_hash clickable** with mock Arc-explorer URL → `https://explorer.arc.network/tx/{hash}` (per §5.10).
7. **Filter leaderboard** to canonical 4 agents (or add a "test/seed" toggle) so the visual story is clean.

## Demo readiness verdict
**YELLOW (leaning RED for the deep-dive sections).**

**What works for a 60-second demo:**
- Home page is polished and on-brand
- Trigger button → redirect → 7-phase + 11-node "all Done" visual is satisfying at a glance
- Leaderboard + agent profile + history + about all render and tell a coherent story
- React Flow workflow DAG matches §5.31 exactly

**What will fail under judge scrutiny:**
- "Show me the multi-agent debate" → there is no debate trace anywhere in UI
- "Show me the 11-judge scores" → only 8 booleans, no numeric scores, no BLEU/COMET/MQM
- "Show me real-time SSE" → connection opens but no events flow
- "Click into the on-chain TX" → no link
- "Show me the actual translated question" → API has it, UI hides it
- Phases 3-7 are functionally placeholders

The infrastructure is in place; the rendering of inline phase content is the gap. **~1-2 days of frontend work** to bring this from YELLOW to solid GREEN — primarily wiring `phases[].details` into the existing `EventTimeline` listitems.

---

*Audit driven by Playwright MCP. Screenshots in `outputs/screenshots/audit_*.png` (8 total). Event under test: #81. Backend `localhost:8000`, Frontend `localhost:3001`. No source code modified. No commits made.*
