# PolyglotAlpha v2 — End-to-End Demo Validation Report

**Run timestamp:** 2026-05-25 ~15:33 PT
**Tester:** automated harness (Claude Code)
**Backend:** `polyglot_alpha.api.main:app` on `127.0.0.1:8000` (uvicorn, fresh `polyglot_alpha.db`)
**Frontend:** `next dev -p 3001` (3000 was occupied by an unrelated Boxxo dev server)

---

## 1. Backend startup

- **Started:** YES, clean.
- Log excerpt:
  ```
  INFO:     Started server process [85522]
  INFO:     Waiting for application startup.
  INFO:     Application startup complete.
  INFO:     Uvicorn running on http://127.0.0.1:8000
  ```
- No tracebacks, no warnings, no errors throughout the full session.

## 2. Endpoint sanity (cold DB)

| Endpoint               | HTTP | Response (first ~200 chars)                                                                |
|------------------------|------|--------------------------------------------------------------------------------------------|
| `GET /health`          | 200  | `{"status":"ok"}`                                                                          |
| `GET /events`          | 200  | `{"items":[],"limit":50,"offset":0}`                                                       |
| `GET /leaderboard`     | 200  | `{"sort_by":"cumulative_fees","items":[]}`                                                 |
| `GET /events/1`        | 200  | `{"id":1,"content_hash":"f278e5...","sources":[...],"language":"en","title":"...","triggered_at":"...","status":"REJECTED"}` |
| `GET /events/1/bids`   | 200  | `{"event_id":1,"items":[{"id":4,"agent_address":"0xllama_agent","bid_amount":0.95,"stake_amount":5.0,...}, ...]}` |

All five endpoints return well-formed JSON. No 5xx.

## 3. Triggered events (`POST /trigger/event`)

Each call returns `event_id`, `status`, `verdict`, `overall_score`. SSE confirmed full lifecycle for each (auction → bid → settle → translation → quality).

| # | event_id | Topic (truncated)               | # bids | Highest bid (winner per SSE) | Verdict | Score | DB status |
|---|----------|---------------------------------|--------|-------------------------------|---------|-------|-----------|
| 1 | 1        | PBoC RRR cut before Aug 23 2026 | 4      | `0xllama_agent` @ 0.95        | FAIL    | 0.10  | REJECTED  |
| 2 | 2        | SpaceX Starship Flight 12       | 3      | `0xqwen_agent` @ 0.80         | FAIL    | 0.10  | REJECTED  |
| 3 | 3        | Fed cut at Sep 2026 FOMC        | 2      | `0xgemini_agent` @ 0.85       | FAIL    | 0.10  | REJECTED  |

Three independent events tracked separately with correct IDs and content hashes.
Auction winner selection (highest bid) is correct in every case.

`market_id` / fills do NOT appear in any response — expected since all events
fell below the `QUALITY_PASS_THRESHOLD=0.7` gate in `orchestrator.py:64`, so the
Polymarket submit phase is skipped. The 0.1 fallback score is the
`raw_score / 100.0` clamp on an empty mock panel result.

## 4. SSE stream (`GET /sse/events`)

Captured 48 lines over 12 s window covering events 2 and 3. Event types observed:

- `hello` (subscriber ack)
- `event.created` (with `event_id`, `content_hash`)
- `auction.opened` (with `tx_hash`, `window_s`)
- `bid.submitted` (one per bid, with `agent_address`, `bid_amount`)
- `auction.settled` (with `winner_address`, `winning_bid`, `tx_hash`)
- `translation.completed` (with `translator_address`, `candidate_hash`)
- `quality.verdict` (with `verdict`, `overall_score`)

No `market.*` events emitted (consistent with all-FAIL verdicts).

## 5. Leaderboard after 3 events

```json
{"sort_by":"cumulative_fees","items":[
  {"rank":1,"agent_address":"0xgemini_agent","total_bids":3,"total_wins":0,"avg_quality":0.0,"cumulative_fees":0.0},
  {"rank":2,"agent_address":"0xdeepseek_agent","total_bids":2,"total_wins":0,"avg_quality":0.0,"cumulative_fees":0.0},
  {"rank":3,"agent_address":"0xqwen_agent","total_bids":2,"total_wins":0,"avg_quality":0.0,"cumulative_fees":0.0},
  {"rank":4,"agent_address":"0xllama_agent","total_bids":2,"total_wins":0,"avg_quality":0.0,"cumulative_fees":0.0}]}
```

- `total_bids` aggregates correctly (3, 2, 2, 2 matches the inputs).
- `total_wins` is always 0 — see Bug B1.
- `avg_quality` / `cumulative_fees` always 0 — gated on PASS verdicts (no PASSes in this run).

## 6. Frontend

- **Started:** YES on port 3001 (port 3000 already taken by an unrelated Boxxo Localization dev server — not a Polyglot bug).
- `GET http://127.0.0.1:3001/` returned `HTTP 200`.
- `<title>Polyglot Alpha v2</title>` and meta description "Decentralized cross-language alpha pipeline: from event ingestion to on-chain anchoring." present.
- Hero visible content includes:
  - "Polyglot Alpha v2", badge "mainnet-mock", tag "v2 · cyber pricing engine"
  - Headline: "Decentralized cross-language alpha, from headline to on-chain anchor in under …"
  - CTAs: "Explore live events", "Trigger live demo"
  - Feature cards: "Cross-language alpha", "Verifiable pipeline", "Streaming builder fees"
  - Workflow nodes: Event Ingest, Preprocess + NER, USDC Auction, Translation L1–L5, Analyst Debate, 11-Judge Panel, Polymarket Submit, Revenue Stream, Reputation Update
- Compile time: 4.1 s, 988 modules. No warnings or errors in `/tmp/polyglot-frontend.log`.

## 7. Bugs / gaps found

Prioritized:

- **B1 (Sev: high, UX-blocker for demo).** `GET /events/{id}` and `GET /events/{id}/bids` do NOT expose `winner_address`, `verdict`, `overall_score`, `candidate_hash`, `market_id`, or quality breakdown — although `Bid.winner_address` (models.py:109), `QualityScore.verdict / overall_score` (models.py:147-148), and `market_id` fields (models.py:179) all exist in the DB. The trigger response carries this info briefly, but a page refresh loses it. The UI's event detail view cannot display the lifecycle without these fields.
  - **Fix:** extend `_serialize_event()` in `polyglot_alpha/api/routes/events.py:16-25` to JOIN/include latest `Bid.winner_address`, `QualityScore.verdict + overall_score`, and `Translation.candidate_hash + market_id`. Also include `winner_address` / `candidate_hash` per-bid in `list_bids_for_event` (the `winner_address` column is silently dropped in the response).
- **B2 (Sev: medium, demo polish).** Demo always returns `overall_score=0.1` / `verdict=FAIL` for every event, so a Loom recording would show zero "PASS → Polymarket submit → fees" success path. The `_run_judges` fallback in `polyglot_alpha/orchestrator.py:202-244` returns 0.1 when the panel is mocked. To record a winning path, either lower `QUALITY_PASS_THRESHOLD` for demo (e.g. 0.0) or have `_run_judges` return a stub PASS in mock mode.
  - **Fix:** export `QUALITY_PASS_THRESHOLD=0.0` for the demo env, or add a `MOCK_PANEL_VERDICT=PASS` env switch.
- **B3 (Sev: low).** Leaderboard `total_wins` is computed from PASS-only criteria, so with all-FAIL runs the agent who actually won the auction shows `total_wins=0`. Consider distinguishing "auction wins" from "quality-passed wins" in the response.
- **B4 (Sev: low, cosmetic).** SSE has no terminal event (e.g. `event.rejected` or `event.finalized`) when an event fails quality — only `quality.verdict` is emitted, leaving subscribers without an explicit "this event is done" signal. A `event.finalized` event would help the UI close the trace cleanly.

No tracebacks, no 5xx, no validation errors throughout.

## 8. Demo readiness

**Almost.** Backend + UI both start cleanly and the full SSE lifecycle fires end-to-end. The pipeline is solid through the QUALITY phase. To record a compelling Loom:

1. **Required:** address B2 so at least one demo event reaches PASS and the Polymarket/builder-fee path actually executes — otherwise the Loom ends at "FAIL" every time.
2. **Strongly recommended:** address B1 so the event detail page can render winner/score/market data after page reload.

Without those two fixes, the recording would only demonstrate the front half of the pipeline (auction → bid → settle → translate → quality FAIL) with no on-chain anchor or builder fee visible.

## 9. Cleanup

- Backend uvicorn process (PID 85522) killed.
- Frontend Next dev process (PID 7201) killed.
- PID files removed from `/tmp`.

## 10. Log files (for follow-up)

- `/tmp/polyglot-backend.log` — clean, only INFO lines.
- `/tmp/polyglot-frontend.log` — clean compile, no warnings.
- `/tmp/sse_capture.log` — 48 lines of SSE events from events 2 and 3.
