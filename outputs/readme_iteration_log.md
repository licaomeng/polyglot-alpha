# README Iteration Log

> Snapshot trail of the polyglot-alpha README rewrite, May 2026.
> Each iteration target + what actually changed.

---

## Iteration 1 — Draft (515 lines)

**Goal**: complete first cut of all 13 structural sections from the brief.

**What landed:**
- TL;DR with the PBOC headline anchor + $33.5B Polymarket volume + builder code
- "The Problem" section opening with the 14:32 CST wire trace (no analogy, just the scenario)
- 4 Mermaid diagrams: 10+1 component graph (LR), open/closed IP boundary (TD), 5-contract sequence diagram, builder code fee flow
- The Mechanism / Why It Works / Closed Evaluator IP triad
- Translator agent table (4 agents × provider × specialty × bid posture)
- 11-judge panel: 3 translation sub-panel + 8 style-alignment sub-panel + triangulated aggregation rule (code block)
- On-chain Architecture: 5 contract addresses + bytecode size + role + Arc capabilities table
- Polymarket V2 builder code: builder address + code + fee structure + 3-tier demo mode + safety nets
- Real vs Mock honest accounting (16-row classification table)
- Numbers table (12 measured metrics with source pointers)
- How To Run It (5-step curl recipe)
- Backend API + Frontend route tables
- Mechanism design defaults (locked) table
- Audit + hardening pass (8 audit reports, 6 fix waves)
- License (3 tiers) + Roadmap (4 phases) + Closing Thesis

**Snapshot**: `outputs/readme_v1.md`

---

## Iteration 2 — Depth Pass (653 lines, +138)

**Goal**: identify shallow spots, add 2+ Mermaid diagrams, deepen technical detail, sharpen code references.

**Critique of v1:**
- 5-layer pipeline (the actual work the winner performs) mentioned in one cell of the component table but never explained
- Numerai parallel claimed twice without visual support
- Polymarket-volume-by-language gap discussed in prose but no diagram
- No end-to-end worked example with real numbers — the lifecycle is abstract

**What landed in v2:**
- New Mermaid #5: Polymarket volume gap (English top-5 vs non-English best-effort)
- New Mermaid #6: Numerai-class parallel (individual quant vs Renaissance, individual builder vs PolyglotAlpha)
- New Mermaid #7: 5-layer pipeline (Source Analysts → Bull/Bear debate → Synthesizer → Risk panel → final output), with code-path annotations on each layer
- New section: "What the Winning Agent Actually Runs" — full L1-L5 walkthrough with code refs to `polyglot_alpha/analysts.py`, `translators.py`, `synthesizer.py`
- New section: "End-to-End Worked Example" — 127-second timeline from T+0 RSS pickup to T+127s bid release, with bid table showing all 4 agent bids, reputation deltas, and the 6,000× cost-to-revenue calculation

**Snapshot**: `outputs/readme_v2.md`

---

## Iteration 3 — Polish + Medium Emulation (653 lines)

**Goal**: compare structure/voice to `info/_Medium/group-1-ai-translation-quality`, tighten transitions, sharpen hooks at section starts, trim filler, verify every claim cross-referenced.

**Critique of v2:**
- Section openers were declarative (telling) instead of arresting (showing)
- "100K markets" claim inconsistent with measured 1921 markets pulled from corpus — over-promised
- Cross-references concentrated in 4-5 sections; many sections had zero §5.X anchors
- Transitions between major sections were too clean — felt like a list of topics, not an argument

**What landed in v3:**
- Sharpened 6 section openers to lead with tension or a counterintuitive claim:
  - "How Each Translator Agent Differs" now opens: "If the moat is tacit knowledge, the agents need to actually have different tacit knowledge..."
  - "11-Judge Panel" now opens: "A perfectly faithful translation can still be an unusable Polymarket question."
  - "On-Chain Architecture" now opens: "The judges' verdicts have to be permanent and the fee routing has to be unforgeable. Both push the system onto chain."
  - "Polymarket V2 Builder Code" now opens: "Until May 2025, Polymarket markets were attributed to 'Polymarket.' Builder Codes changed that."
  - "Real vs Mock" now opens: "A hackathon README that doesn't say which parts are mocked is doing its reviewer a disservice."
- Reconciled "100K markets" → "1921 markets pulled, $6.99B aggregate volume" (matches `corpus/polymarket_v2026_05.parquet` actual)
- Added §5.X anchors in: TL;DR (3 new), Problem section (1), On-Chain section (2), Builder Code section (2), Mechanism Defaults (1), Audit section (1), Closing Thesis (2)
- Final tally: **39 §5.X cross-references** (target ≥ 30) across **19 unique sections**

**Snapshot**: `outputs/readme_v3.md` (= final `README.md`)

---

## Final Metrics

| Metric | Target | Final |
|--------|--------|-------|
| Line count | < 1000 | 653 |
| Mermaid diagrams | ≥ 6 | 6 |
| Code blocks (env / curl / aggregation) | ≥ 3 | 18 (incl. inline) |
| Cross-references to §5.X | ≥ 30 | 39 mentions across 19 unique sections |
| Honest scope statement present | Y | Y (TL;DR + Real-vs-Mock + Closing Thesis) |
| Iterations completed | ≥ 3 | 3 (v1 → v2 → v3) |

---

## Most-Medium-Quality Section

**"End-to-End Worked Example"** (~447–470) — single 127-second timeline with bid table, real TX hash example, 6,000× cost-to-revenue calculation. Reads like the Article-1 "How an Analysis Run Works" section in the Medium reference: discrete steps with numbers attached, not abstract claims.

## Outstanding Gaps

- The 4 pre-rendered `submission/diagrams/mmd_*.png` are referenced but not embedded inline. They live alongside the README; reviewers viewing on GitHub will see the inline Mermaid blocks render natively. PDF/Loom flows should use the PNG fallbacks.
- The §5.X anchor hashes are best-guess (e.g., `#542-why-we-do-this-complex-pipeline`). GitHub generates anchors deterministically from heading text, but special characters in the thesis headings (em dashes, asterisks) may slightly differ. Spot-check 2-3 anchors after pushing the thesis.
- The "Roadmap" section dates ($300 deploy gas / $1500 infra / $3K/mo) are post-hackathon projections from §5.51; not measured.
- Demo video (`submission/demo_script.md` referenced, `outputs/demo_video/polyglot_alpha_demo_v1.mp4` placeholder) is not yet shipped in this README — section deferred to thesis §5.50.
