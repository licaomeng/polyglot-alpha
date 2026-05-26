# Evaluator Trial — Hackathon Judge Pass

**Persona:** Agora Agents Hackathon judge, ~5 min per project.
**Frontend:** http://localhost:3001 (Next.js 15.5.18 dev server)
**Backend:** http://localhost:8000 (FastAPI, healthy)
**Date:** 2026-05-26 06:18–06:23 SGT

---

## Verdict

**Cannot evaluate the UX. Every route on the frontend returns HTTP 500.**

If I were judging right now, I would walk away after 30 seconds. The backend is excellent and the demo *would* land — but a judge does not read JSON. They click "Trigger live demo" and watch.

| Criterion | Score (1–10) | Note |
|---|---|---|
| First-impression credibility | **1** | Browser shows raw "Internal Server Error" plaintext on `/` |
| Demo "wow" moment | **1** | Trigger button never visible; cannot click |
| Technical depth visible to non-expert | **2** | All depth is hidden behind a 500 — only visible via curl |
| UX polish | **1** | Zero UI rendered |
| On-chain story landing | **2** | Anchor txHash + Arcscan URL exist in API, but no judge will see them |

**Would I fund this?** No — not on this run. The pitch is real, but the demo is dark.

---

## Lifecycle Walk (intended → actual)

1. Land on `/` → **500 Internal Server Error** (plaintext, no styling, no nav).
2. Click "Trigger live demo" → impossible, button never rendered.
3. Navigate to `/events` → **500**.
4. Click a PASS event → **500**.
5. `/leaderboard` → **500**.
6. `/about` → **500**.
7. `/agents` → **404** (route doesn't exist in app router despite folder).
8. `/history` → **500**.

Screenshots in `outputs/evaluator_trial/`:
- `01_landing_500_error.png` — initial GET /
- `02_leaderboard_500.png` — leaderboard 500
- `03_about_500.png` — about 500
- `04_landing_final_state.png` — re-tested final state, still 500

Dev-server log (`/private/tmp/polyglot-frontend.log`) shows the root cause:

```
⨯ Error: Cannot find module './611.js'
⨯ Error: Cannot find module './86.js'
[Error: ENOENT: open '.next/fallback-build-manifest.json']
⨯ Error: Could not find the module "...next-devtools/userspace/app/segment-explorer-node.js#SegmentViewNode" in the React Client Manifest.
[TypeError: __webpack_modules__[moduleId] is not a function]
```

The `.next/` cache is corrupted. The chunk file `.next/server/chunks/611.js` *does* exist on disk, but Next.js's manifest references missing files and the dev server cannot recover without `rm -rf .next && next dev`.

---

## Issues That Would Lose Points (judge-visible)

### 1. CRITICAL — Frontend SSR is dead on every route
The single most damaging defect possible during a live demo. As a judge I see one of these on first paint and immediately downgrade. The fix is a one-line cache wipe; the operational discipline gap is what worries me.

### 2. HIGH — Latest event headline is smoke-test garbage
`/events` (via API) returns event 127 with title `"Final retest 1779747116673801000"`. A judge will see this on the events list. If a smoke test runs against prod-shaped data, the cleanup is missing. Production-grade demos use a deterministic seed or hide test-mode events.

### 3. MEDIUM — Judge phase payload is reductive
The "11-Judge Panel" phase only exposes `{verdict, overall_score}`. The pitch promises an "11-judge consensus" but the API doesn't return individual scores, dissent count, or per-judge rationales. A skeptical judge will read this as marketing > substance.

### 4. MEDIUM — `tx_hash` is null on every bid in `/events/{id}/bids`
Anchor txHash is populated, but each bid's `tx_hash: null`. The on-chain story claims "every phase is cryptographically attestable" — half the phases lack real chain references.

### 5. LOW — Translation `confidence` and `quality_score` are constants (0.5)
`final_question.confidence: 0.5` and `quality_score: 0.5` across every event I inspected. Either they're placeholder or unused — but they look hard-coded.

### 6. LOW — `/agents` route 404s
The frontend has an `app/agents/` folder but the route returns 404. Either dead code or broken routing — both look sloppy.

---

## Things That Surprised Me Positively

### 1. POS — The backend data shape is genuinely rich and credible
A real `/events/{id}` payload returns: pipeline phases with per-phase `details`, an Arcscan-resolvable `txHash`, an `ipfs://` CID for pipeline trace, an MQM translation-error breakdown with categories (Accuracy/Fluency/Terminology/Style) + severities (MAJOR/MINOR) + per-error rationales, an 8-dim `style_alignment_passes` matrix, a Polymarket V2 `builder_code`, real `market_url`, `winner_address`, and `winning_bid`. If the UI rendered this, the demo would absolutely land.

### 2. POS — Trigger endpoint is fast and fully synchronous-with-payload
`POST /trigger/event` with a one-line body returns a populated event in ~1.5s containing the winning bid, verdict, all judge bids, builder code, dryrun market URL, and overall score. That's a real working pipeline — no async polling dance needed for a demo. Excellent operator ergonomics.

### 3. POS — Leaderboard has multi-agent reputation, win-rate, fees
9 agents, varied win rates (1.0, 0.47, 0.45, ...), cumulative fees in USD, reputation scores between 0.59–0.74. This is not a "one-bot wins everything" demo. It looks like a real auction history.

---

## Recommended Fix Order (75-minute window)

1. **(2 min)** Stop the dev server, `rm -rf ui/.next`, restart `next dev`. Verify all four routes return 200.
2. **(5 min)** Curl every route after restart and screenshot the landing page to confirm.
3. **(10 min)** Hide smoke-test events whose title matches `/^Final retest \d+$/` or `/^perf-bench-/` from the `/events` list response. Either add a `mode!=test` filter or backfill titles.
4. **(15 min)** Populate `tx_hash` on bids (even with a deterministic mock hash) so the auction phase looks attestable.
5. **(Optional)** Expand the 11-Judge phase details to expose per-judge scores in the API response. If time-bounded, at least add `judge_count`, `pass_count`, `mean_score`.

The Next-cache failure is the only blocker. Everything else is polish.
