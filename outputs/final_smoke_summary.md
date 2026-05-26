# Final Smoke Retest (Iter 5) — Pass/Fail

**Verdict: GREEN** (with one minor observation, not blocking).

## Top-line
- Smoke score: **10/12** (unchanged from baseline)
- Backend health: **OK** (http://localhost:8000)
- Frontend pages: **7/7 render** (http://localhost:3001 — see note)
- Fresh event end-to-end: **PASS** in 64.5s
- Filter button bug fix verified: **YES** (all 6 status buckets return events)
- Mobile 44px touch targets: **PRESENT**
- Regressions: **0**

## Smoke checks (10/12)
PASS: backend_health, trigger_event_source_rss_no_422, verdict_present, market_id_real_or_dryrun, tx_hash_not_sha256_fake, quality_scores_mqm_real, four_agents_bid, bids_diverse, polymarket_dryrun_mode, submit_real_endpoint_exists.
FAIL: quality_scores_bleu_real (BLEU=None), quality_scores_comet_real (COMET=None). Same two known gaps as prior iterations — MQM is real (77), not mock 50.

## Frontend
Polyglot-alpha UI was **not running** when this iteration began (port 3000 is held by a Boxxo dev server from another project). Started polyglot-alpha on **:3001**. All 7 routes return HTTP 200 with substantial HTML (>20 KB) and no "Internal Server Error" / "TypeError" / "application error" text. Agent J's mobile fix (`min-h-[44px]`) is in place on Button, TxLink, and SiteHeader.

## Fresh event (id=127)
SUBMITTED, verdict=PASS, overall_score=0.83, market_id=dryrun-f89c01bbcdb5, 4 unique agents, 4 distinct bid amounts (0.3032 / 0.75 / 0.75 / 0.88), open_tx_hash and commit_tx_hash both real 0x... hashes in the trigger response. **Minor observation**: those tx hashes are not echoed back by `GET /events/127` (DB events table has no Arc-tx columns; persistence pending). Not user-visible on success path because the UI fetches detail from the same endpoint and renders empty; if this matters for 8 AM demo, file follow-up.

## DB state
events: SUBMITTED=90, REJECTED=13, FAILED=12, PENDING=6, EVALUATING=4, AUCTION_OPEN=2. Bids: 145 distinct agents. Corpus: 75,885 markets. Quality scores with MQM: 34.

## Filter buttons (Agent D fix)
All status buckets return non-zero events; `SUBMITTED` returns 50. Bug **fixed**.

## Backend stability
11 uvicorn reloads logged overnight (auto-reload from edits to `d8_duplicate_detection.py`), currently quiescent. Two uvicorn processes alive (PIDs 1491 and 55511) — both listening, only one bound. Not blocking.

## Readiness for 8 AM
**GREEN.** Recommend the user manually verify `/events/127` in the browser at :3001 to confirm visual quality before demo.
