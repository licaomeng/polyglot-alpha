# PolyglotAlpha v2 — 3-Minute Demo Video Script

Target length: **≤3:00** (Field 13 soft cap on the Agora submission form).
Format: Loom screen recording, voiceover, subtitles burned in via Loom auto-captions.
Aspect ratio: 16:9, 1080p. Loom recording at 30 fps is fine.

This script reflects the **Phase 1 ship state** (§5.48 decisions locked): a real 60-second on-screen lifecycle driven by the "Trigger live demo" button, real Arc transactions, real LLM judge scores, and Polymarket submission in `dry_run` mode by default (real submission gated behind a UI toggle).

## Structure

| Block | Duration  | Topic                                                                          |
|-------|-----------|--------------------------------------------------------------------------------|
| A     | 0:00–0:20 | Problem (Polymarket misses non-English alpha)                                  |
| B     | 0:20–0:40 | Solution overview (24/7 multilingual + 11-judge QC + builder-fee revenue)      |
| C     | 0:40–1:00 | Live demo intro ("watch real lifecycle in 60 seconds")                         |
| D     | 1:00–1:55 | Live demo execution — Phases 1–6 with real on-chain TXs                        |
| E     | 1:55–2:15 | Defensible moat (open contracts / closed evaluator IP)                         |
| F     | 2:15–2:40 | Numbers (corpus + volume + languages + builder fee)                            |
| G     | 2:40–3:00 | Closing (proof of mechanism + roadmap + ask)                                   |

Voiceover budget: 3 min at 150 wpm ≈ **450 words total**. Current body ≈ 430 words.

---

## Block A — Problem (0:00–0:20)

| Time      | Visual                                                                                | Voiceover                                                                                                                                                                                            | Tone           |
|-----------|---------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------|
| 0:00–0:08 | Polymarket homepage; cursor pans across all-English category tiles.                   | "Polymarket misses non-English alpha. Seventy percent of its markets are community-submitted, but ninety-five percent of submitters speak English."                                                  | matter-of-fact |
| 0:08–0:20 | Split screen: Caixin / Xinhua / Nikkei headlines in CJK on the left; empty Polymarket non-English category on the right. | "The Mandarin macro headline that moves rates futures by 3 a.m. UTC never reaches Polymarket, because human curators take a day to translate it into a well-formed prediction question."             | direct         |

---

## Block B — Solution overview (0:20–0:40)

| Time      | Visual                                                                                          | Voiceover                                                                                                                                                                                          | Tone        |
|-----------|-------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| 0:20–0:30 | Title card: "PolyglotAlpha v2." Three sub-bullets fade in: "24/7 multilingual pipeline / 11-judge QC / builder-fee revenue stream". | "PolyglotAlpha is a translation auction on Arc. Four LLM agents bid USDC for the right to translate. An eleven-judge panel scores the output. A builder code routes maker fees back to us per fill." | confident   |
| 0:30–0:40 | Workflow DAG (`/` page) — 10+1 component graph, edges pulsing left to right.                    | "Ten components plus a Polymarket corpus. Real Arc contracts. Real LLM calls. One end-to-end lifecycle, sixty seconds wall-clock."                                                                  | crisp       |

---

## Block C — Live demo intro (0:40–1:00)

| Time      | Visual                                                                                       | Voiceover                                                                                                                                                                              | Tone        |
|-----------|----------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| 0:40–0:50 | Navigate to `/events` page. Cursor hovers on **"Trigger live demo"** button.                 | "Here's what's different from a slide deck: I'm going to click this button, and you'll watch the real lifecycle execute in sixty seconds."                                              | demonstrative |
| 0:50–1:00 | Click button. SSE event-stream begins. Phase 1 pill turns amber, then green.                 | "Real RSS pull. Real Arc transactions. Real LLM judges. Polymarket submission is on dry-run for safety — there's a toggle if you want it real."                                          | live        |

---

## Block D — Live demo execution (1:00–1:55)

| Time      | Visual                                                                                                                       | Voiceover                                                                                                                                                                                                | Tone        |
|-----------|------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| 1:00–1:15 | Phase 1 timeline card expands: RSS aggregator pulls a Caixin / Xinhua headline. Phase 2 opens: four bid rows animate in.     | "Phase one: RSS aggregator pulls the latest Mandarin or Japanese news — this is the real feed, not a fixture. Phase two: four LLM agents place bids on Arc via real USDC transactions."                  | brisk       |
| 1:15–1:35 | Phase 3 panel: 5-layer pipeline cards flip PENDING → RUNNING → COMPLETED. Phase 4 panel: 11-judge grid renders with scores.   | "Phase three: the winning agent runs the five-layer pipeline. Phase four: the eleven-judge panel scores it — BLEU sixty-two, COMET zero point four-nine, aggregate MQM ninety-two. Hard gates pass."     | careful     |
| 1:35–1:55 | Phase 5: TX hash on `QuestionRegistry`; click opens Arc explorer in adjacent tab. Phase 6: Polymarket submission card flips to "DRY-RUN" badge. | "Phase five: the question is anchored on Arc — click the explorer to verify. Phase six: Polymarket submission, dry-run by default. In a real run it would route to our builder code starting `0xa934`." | proud       |

---

## Block E — Defensible moat (1:55–2:15)

| Time      | Visual                                                                                       | Voiceover                                                                                                                                                                          | Tone        |
|-----------|----------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| 1:55–2:05 | Two-column slide. Left: "OPEN — MIT" (contracts, submit API, reputation rule). Right: "CLOSED — core IP" (judge weights, corpus, thresholds, anti-pattern algorithms). | "The smart contracts are MIT, open source. Anyone can fork them, register an agent, and bid. The evaluator IP — the eleven-judge weights and the corpus — stays closed."          | deliberate  |
| 2:05–2:15 | Right column annotations: "Moody's pattern", "S&P pattern", "FICO pattern".                  | "Same configuration Moody's and S&P use: publish the rubric concept, keep the specific weights private. If everyone knows the score, the auction collapses into a price war."     | analytical  |

---

## Block F — Numbers (2:15–2:40)

| Time      | Visual                                                                                       | Voiceover                                                                                                                                                                                       | Tone        |
|-----------|----------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| 2:15–2:25 | Stats card: "100K Polymarket markets scraped · $34.8B aggregate volume · 8 languages".        | "By the numbers: one hundred thousand Polymarket markets scraped for the corpus. Thirty-four point eight billion dollars of aggregate volume across that corpus. Eight languages supported."    | factual     |
| 2:25–2:40 | Builder-fee math card: "0.4% per fill · 40 bps × taker volume · streamed in perpetuity".      | "Maker fee zero point four percent per fill, effective May twenty-ninth. Streamed to the winning translator wallet for the lifetime of each question — that's the revenue mechanism."           | mechanical  |

---

## Block G — Closing (2:40–3:00)

| Time      | Visual                                                                                       | Voiceover                                                                                                                                                                                      | Tone        |
|-----------|----------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| 2:40–2:50 | Two columns: "Today — proof of mechanism" vs "6–12 mo — real third-party bidder ecosystem".  | "Today: proof of mechanism. Real contracts, real agents, real judges, real lifecycle. Real third-party bidder ecosystem is a six-to-twelve-month build, honestly scoped."                       | level       |
| 2:50–3:00 | Final card: "Ask: scout funding · Circle intro · Polymarket intro". Handle `licaomeng`.       | "Ask: scout funding, a Circle intro for CCTP V2 production wiring, and a Polymarket intro for one live testnet market. Repo and contact on screen. Thanks for watching."                        | grateful    |

---

## Production notes

- **Voice:** unhurried; resist the urge to cram. Better to land Block D with 5 seconds of dead air than to rush a judge over the gate cutoffs or the TX hash reveal.
- **Cursor:** keep mouse movement deliberate; pause on every TX hash for ≥1 second so judges can pause-and-verify if they want.
- **Subtitles:** Loom auto-captions, then manual scrub for "PBOC", "Polymarket", "Arc", "MQM", "COMET", "FAISS", "EWMA", "Caixin", "Xinhua" — auto-caption mis-hears all of these.
- **Audio:** record dry, no music. Hackathon judges sit through ~280 submissions; background music is a stress amplifier.
- **Thumbnail:** title card from 0:20 (PolyglotAlpha v2 + three-bullet sub-tagline) as Loom thumbnail.
- **Live demo safety:** keep the Polymarket submission toggle on `dry_run` for the recorded take; the real-submit path exists but rate-limited (max 5/day) and gated by the 11-judge quality threshold per §5.43.
- **Length safety:** if the take runs 3:00–3:10, do not re-record — Field 13 says "≤3 min recommended (soft, not validator-enforced)." Inside 3:15 is safe.
- **Watermark:** leave the Loom logo on; do not pay for removal — hackathon judges respect zero-budget production.

## Word count sanity

Voiceover body across all seven blocks ≈ **430 words**. At 150 wpm = 172 seconds ≈ **2:52**. Leaves 8 seconds for opening/closing visual beats. Target landing: **3:00 flat**.
