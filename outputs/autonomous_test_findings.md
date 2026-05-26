# Autonomous Test Loop Findings (T1 sub-agent)

Start: 2026-05-26
Backend: http://localhost:8000 (200 OK)
Frontend: http://localhost:3001 (200 OK)

Plan: 5+ sequential passes of the demo flow with DAG-node linkage validation.

---

## Pass 1 — 2026-05-26 ~03:54-04:02 UTC

Summary: PARTIAL ✗ (1 HIGH content-truth bug; flow itself works)

### Flow result
- Trigger demo → instant nav to /events/20 ✓ (sub-100ms client-side route)
- Phase progression observed: 0/1 done very fast → STEP03 USDC Auction Running → STEP04-06 ran in parallel → terminated at REJECTED after STEP07 Judges completed but STEP08-11 marked failed.
- Final status: REJECTED, status badge correct, no 5xx, no console errors during run (only favicon.ico 404)
- DAG → Timeline linkage tested for nodes `auction`, `judges`, `anchor`, `pmsubmit` — ALL hook into the correct phase-card-N element with `.ring-accent` spotlight class. ✓ (this was the user's priority)

### HIGH severity bugs
- **B1 — content/truth: /operators + /about lie about seeders.** UI hardcoded "4 reference seeders: Mistral Large / DeepSeek V3 / Qwen 2.5 / Llama 3.3" with OpenRouter-style model IDs and one fabricated wallet (`0x70a0…3F2a` for Mistral and `0xC95D…56F0` for Llama do NOT appear in outputs/agent_wallets.json). Backend reality is 3 seeders (gemini / deepseek / qwen), all routing to Claude Haiku 4.5 (polyglot_alpha/llm.py:14). UI also had "Llama 3.3 70B (OpenRouter)" provider string in AgentProfile.

### MEDIUM severity bugs
- **B2 — data inconsistency: /operators hardcoded stats vs /leaderboard live data.** /operators says "Auctions Settled 37, Builder Fees Paid $242.20"; /leaderboard fetched-from-backend shows 0 revenue, 0 win rate for all 3 known seeders. Either /leaderboard should reflect bootstrap stats or /operators should drop the mock numbers. Did not fix — needs backend `/operators` endpoint.

### LOW severity
- **B3 — 3 frontend prefetch 404s** on /events/{id}: GET `/agents`, `/markets`, `/judges` return 404 from the Next.js dev server. Initiator not traceable from rendered DOM; probably Next.js Link prefetch with mistyped href somewhere in dev-only code. No user-visible effect. Did not fix — source not located.
- **B4 — favicon.ico 404**, harmless.

### Fixes applied
- `ui/app/operators/page.tsx`: dropped 4th seeder, rewrote 3 entries to use wallet addresses from `outputs/agent_wallets.json`, swapped model strings to `claude-haiku-4-5 · <persona>`, updated "4 in-house" copy → "3 in-house".
- `ui/components/operators/OperatorCard.tsx`: doc-comment 4→3.
- `ui/app/about/page.tsx`: rewrote two paragraphs + 10+1 components row to advertise 3 Claude-Haiku-personas (Gemini / DeepSeek / Qwen) instead of 4 OpenRouter models.
- `ui/components/event/AgentDebatePanel.tsx:416`: "4 reference seeder agents" → "3 reference seeder agents".
- `ui/components/reputation/AgentProfile.tsx`: dropped llama entry + "(OpenRouter)" suffix, replaced all 3 provider strings with `Claude Haiku 4.5 · <persona>`.

### Screenshots
- `outputs/test_screenshots/pass_1_step_1_phase_running.png` — early auction phase
- `outputs/test_screenshots/pass_1_step_4_final_event.png` — final state on /events/20
- `outputs/test_screenshots/pass_1_step_6_operators_fixed.png` — operators after fix

---

## Pass 2 — 2026-05-26 ~04:03-04:06 UTC (event 21)

Summary: ✗ NEW HIGH bug discovered in DAG SSE handler

### Flow result
- Trigger → instant nav to /events/21 ✓
- Initial state at t+10s: ingest/preproc/auction completed, status badge AUCTION_OPEN (slight badge lag vs DAG, MEDIUM-low)
- t+90s: translate/debate/synth running, status badge says EVALUATING — backend already advanced but DAG behind
- Final state at t+150s: judges=failed, anchor/pmsubmit/stream/rep=completed (DAG)
- Final status badge: REJECTED ✓
- Backend REST /events/21 says: 11-Judge Panel=completed, On-chain Anchor=failed, ... (INVERTED from DAG)

### HIGH severity bugs
- **B5 — SSE DAG state inversion: terminal lifecycle shows correct phases swapped.** After lifecycle terminates with REJECTED, the DAG shows judges=failed + anchor+pmsubmit+stream+rep=completed. But REST /events/{id} (truth) says judges=completed + downstream=failed. Root cause in `useEventStream.ts`:
  - `quality.verdict` handler with FAIL verdict set judges phase status to "failed" (wrong; judges phase ran successfully)
  - Then `onchain.committed`/`polymarket.submitted`/`event.finalized` handlers force-set downstream to "completed", ignoring lifecycle abort
  - Sticky-failed protection only worked when previous status was already "failed"
- Reload (full RE-fetch from REST) shows correct state, confirming this is purely an SSE reducer bug.

### Fixes applied
- `ui/hooks/useEventStream.ts:161-189`: `quality.verdict` handler now sets judges phase to "completed" (it ran), and if verdict ≠ PASS pre-marks all subsequent phases as "failed" so the sticky-failed rule keeps them visually correct when follow-up `onchain.committed`/`event.finalized` events arrive.
- `ui/hooks/useEventStream.ts:183-202`: `builder_fee.accrued` and `event.finalized` now use `nextStatus(prev, ...)` instead of force-setting, so they respect sticky-failed.

---

## Pass 3 — 2026-05-26 ~04:09-04:11 UTC (event 23)

Summary: ✓ FIX VERIFIED

### Flow result
- Trigger → instant nav to /events/23 ✓
- Pipeline progressed through phases (some SSE lag observed between phase transitions but no inverted states)
- Final DAG: ingest/preproc/auction/translate/debate/synth/judges all completed; anchor/pmsubmit/stream/rep all failed
- Final backend REST: Event Ingestion / USDC Auction / Translation Pipeline / 11-Judge Panel completed; On-chain Anchor / Polymarket V2 Submission / Streaming Revenue failed
- DAG ⇄ REST: PERFECT MATCH ✓
- Status badge: REJECTED ✓
- DAG → Timeline linkage: auction → phase-card-1, judges → phase-card-3, anchor → phase-card-4 all spotlight correctly ✓
- 0 backend 5xx, 0 console errors

### Screenshots
- `outputs/test_screenshots/pass_3_final_event23_correct.png`

---

## Pass 4 — 2026-05-26 ~04:12-04:13 UTC (event 24)

Summary: ✗ NEW HIGH bug — status badge stuck on EVALUATING after lifecycle ends

### Flow result
- DAG phases all reached correct terminal state (judges=completed, anchor onwards=failed) ✓
- BUT the page header status badge still showed "Judging (EVALUATING)" 60 seconds after backend overall status flipped to REJECTED.
- DAG ⇄ REST mismatch — DAG was right but badge was wrong.

### HIGH severity bugs
- **B6 — status badge stale after lifecycle terminates.** `useEvent(id)` (react-query) caches the REST snapshot taken at page-load time; only `event.updated` SSE invalidates it. When the lifecycle finishes (REJECTED / SETTLED / FAILED), the top-level `status` field changes but no refetch happens, so the badge stays on whatever status the event had at first paint (usually EVALUATING for fast pipelines, AUCTION_OPEN for slow).

### Fixes applied
- `ui/app/events/[id]/page.tsx:34-49`: invalidate `["event", id]` query also on `quality.verdict` and `event.finalized` (in addition to the existing `event.updated`). Forces a REST refetch so the badge transitions to the terminal status.

---

## Pass 5 — 2026-05-26 ~04:14-04:16 UTC (event 25)

Summary: ✓ FIX VERIFIED — full end-to-end clean run

### Flow result
- Trigger → instant nav to /events/25 ✓
- Pipeline progressed through phases with SSE updates working
- Final DAG: ingest/preproc/auction/translate/debate/synth/judges completed; anchor/pmsubmit/stream/rep failed ✓
- Final backend REST: identical phase statuses ✓
- **Status badge: "Rejected" (REJECTED)** — badge now correctly transitions after lifecycle ends ✓
- 0 console errors, 0 backend 5xx, lifecycle terminated cleanly

### Screenshots
- `outputs/test_screenshots/pass_5_final_correct_state.png`

---

## Pass 6 — 2026-05-26 ~04:17-04:19 UTC (event 26)

Summary: ⚠ regression discovered — refetchInterval pauses when tab loses focus

### Flow result
- Triggered, lifecycle ran to completion in backend (REJECTED, verdict=FAIL).
- DAG / status badge stayed stuck at "translate running / EVALUATING" for ~6 minutes while backend was already REJECTED.
- Network tab showed only ~1 refetch/min (not the expected 4s) — `react-query` default `refetchIntervalInBackground: false` pauses polling when tab loses focus (the Loom/Drive tabs the user has open will steal focus often during demos).
- After reload (full mount), DAG + badge updated correctly to REJECTED.

### MED severity bugs
- **B7 — useEvent polling pauses when tab backgrounded.** With multiple Drive / Loom tabs open (which the user always has during demos), focus shifts cause react-query to suspend the 4s polling, so badge + DAG can lag minutes behind backend.

### Fixes applied
- `ui/hooks/useEvent.ts`: added `refetchIntervalInBackground: true` so the event poll continues regardless of tab focus.

---

## Pass 7 — 2026-05-26 ~04:20-04:23 UTC (event 27)

Summary: ⚠ External backend stall (NOT a UI bug)

### Flow result
- Triggered, this run got verdict=PASS (vs FAIL on earlier passes).
- Phases 0-3 (ingest, auction, translate, judges) all completed within ~3 min.
- After judges, lifecycle hung on "Anchor pending" for 5+ min.
- Backend log shows root cause: Anthropic API throwing repeatedly ("Retrying request to /v1/messages..."), 3 judges timing out (comet, d8_duplicate_detection, d7_leading_check timed out @60s, only 8/11 collected). PASS path requires further LLM calls for anchor/submit/stream that never completed.
- Frontend correctly stays on EVALUATING + anchor pending — UI honest about backend state. Network tab confirms /events/27 GETs continuing at expected cadence (refetchIntervalInBackground fix verified).
- 0 frontend 5xx, 0 frontend console errors, 0 backend HTTP 5xx (just Anthropic timeouts re-queuing).

### Notes for user
- This is an external infra issue (Anthropic API congestion + the 60s judge timeout). For the live demo recording, prefer FAIL-path runs since they short-circuit anchor/submit/stream and complete in ~150s; PASS-path can take 5+ min with the current Anthropic retry budget.

---

## Sanity checks (between passes)

- `/history` page (Pass 5 follow-up): loads 25 events, no errors, no console errors
- `/leaderboard`: loads, but shows 0 revenue / 0% win rate for the 3 seeders despite /operators advertising 37 wins / $242 fees → B2 (data inconsistency, MEDIUM)
- backend log (`/private/tmp/polyglot_backend_new.log`): no `500 Internal` rows during entire test loop. Only env-noise warnings: Caixin RSS 404 (known stale source), one transient `Connection reset by peer` from qwen agent + `Connection error` from synthesizer (Anthropic API hiccup, retried successfully).

---

## Final summary

### Files modified (all UI, NO commits, NO backend touched)
1. `ui/app/operators/page.tsx` — 4 fake seeders → 3 real wallet addresses + Claude Haiku 4.5 persona labels
2. `ui/components/operators/OperatorCard.tsx` — doc-comment 4→3
3. `ui/app/about/page.tsx` — paragraph copy + 10+1 components row updated to 3 Claude Haiku seeders
4. `ui/components/event/AgentDebatePanel.tsx` — "4 reference seeders" → "3"
5. `ui/components/reputation/AgentProfile.tsx` — dropped Llama (no backend wallet) + removed OpenRouter mention; all 3 providers now `Claude Haiku 4.5 · <persona>`
6. `ui/hooks/useEventStream.ts` — fixed `quality.verdict` to mark judges=completed (it ran) + pre-mark downstream as failed on REJECT; `event.finalized` + `builder_fee.accrued` now respect sticky-failed via nextStatus
7. `ui/app/events/[id]/page.tsx` — react-query invalidate `["event", id]` also on `quality.verdict` and `event.finalized`
8. `ui/hooks/useEvent.ts` — `refetchIntervalInBackground: true` so polling continues when tab loses focus

### Bug count by severity
- HIGH: 3 — B1 (4 fake/OpenRouter seeders), B5 (DAG status inverted at end), B6 (badge stuck on EVALUATING)
- MED: 3 — B2 (operators vs leaderboard data inconsistency), B7 (refetch pauses when backgrounded), backend stall on PASS path with Anthropic timeouts (external, not a bug)
- LOW: 2 — B3 (Next.js prefetch 404s for /agents /markets /judges), B4 (favicon.ico 404)

### What user MUST check on return
- Walk through Pass 7 background context: PASS path can stall on Anthropic timeouts (5+ min). Reuse FAIL-path runs for the Loom recording — they complete in ~150s and exercise the full DAG → REJECTED transition cleanly.
- The 5 UI files modified are saved in working tree (NOT committed). Test plan: hard-reload http://localhost:3001/ (no cache), then run a fresh demo trigger. Expected: DAG ends with judges=completed + downstream failed, badge=Rejected, all in lockstep.
- Open question for user: should `/operators` show LIVE bootstrap stats (auctions/fees) instead of the current hardcoded mock numbers? They don't match the `/leaderboard` page which reads real data. Backend `/operators` endpoint doesn't exist yet (see hardcoded MOCK_REFERENCE_SEEDERS comment).

---

## T5 stability smoke — 2026-05-26 04:26-04:45 UTC

### Pre-flight
- Backend /health: 200 OK
- Frontend /: 200 OK
- DB had 1 leftover in-flight event (#27) from prior T1 session, triggered 04:20:11 — stuck EVALUATING when I started

### Regression checks (Phase 3 done first, while waiting for #27 to release sema)
- **B1 /operators** [FIXED, STAYS FIXED]: shows 3 reference seeders (Gemini / DeepSeek / Qwen personas), all model `claude-haiku-4-5`, 0 external operators. No Mistral / Llama / OpenRouter / GPT strings anywhere in body text. Counter says "3 Reference Seeders + 0 External Operators".
- **B1 /about** [FIXED, STAYS FIXED]: body matches `/3 seeders/i` and `/claude.*haiku/i`; zero mention of Mistral / Llama / OpenRouter or "4 models" / "four operators".
- **B5 DAG end-state on REJECTED** [FIXED, STAYS FIXED]: tested on event #26 (most recent REJECTED). DAG explicitly shows: STEP 01-07 Completed, STEP 08-11 (Arc Anchor / Polymarket Submit / Revenue Stream / Reputation Update) Failed. Matches REST `/events/26` exactly (phases 1-4 `completed`, phases 5-7 `failed`). No spinning indicators, no "Internal Server Error".
- **B6 status badge** [FIXED, STAYS FIXED]: event #26 badge = "Rejected" (terminal, matches DB). Event #27 (in-flight PASS-path) badge = "Judging" (correctly tracks DB `EVALUATING`). Badge transitions with DB state, NOT stuck.

### Phase 2 triggers
- **Event #27** (carryover, RSS/zh): verdict PASS @ 0.74, but stalled at step 7 (`_commit_question_onchain` → web3 `wait_for_transaction_receipt`). Backend log shows panel collected 8/11 judges (comet, d8_duplicate_detection, d7_leading_check timed out @ 60s). No further orchestrator log lines after `quality.verdict`. UI shows STEP 01-07 Completed, STEP 08-11 Pending — consistent with hang in chain commit. This matches the known "PASS path stalls on Anthropic / Arc RPC timeouts" caveat documented at line 184 above. Lifecycle semaphore = 1 → blocks all subsequent triggers until #27 releases.
- **Event #28** triggered 04:29:54 (RSS/zh): accepted (HTTP 200, status=PENDING, scheduled=true), queued behind #27. Did NOT progress past PENDING within budget because sema still held by #27.
- **Third trigger NOT fired**: with #27 still holding the sema and #28 already queued behind it, firing a third trigger would have just deepened the queue without exercising fresh lifecycle behaviour. Stayed at 2 triggers to avoid pile-up.

### Test data (event_ids tested + final state)
- #27 → EVALUATING (stuck, PASS path, 12+ min) — pre-existing condition
- #28 → PENDING (sema-blocked, never started)
- #26 → REJECTED (regression check passed) — already terminal before T5 started

### New bugs observed
- None new. The #27 stall is the same PASS-path hang documented in line 184 by T1.

### B1 / B5 / B6 status
- B1: FIXED, no regression (verified /operators, /about, AgentProfile copy on /operators page)
- B5: FIXED, no regression (event #26 DAG end-state correct)
- B6: FIXED, no regression (event #26 badge=Rejected, event #27 badge=Judging — both match DB)

### Verdict
- **YELLOW — demo-ready ONLY on FAIL path.** All 3 UI bugs (B1 / B5 / B6) stay fixed. The PASS-path lifecycle hang in `_commit_question_onchain` (`web3.eth.wait_for_transaction_receipt` with no asyncio.wait_for guard) is the dominant operational risk. Mitigations for recording: (a) record on the FAIL path which completes in ~78s and exercises the full DAG → REJECTED transition; (b) before any retake, restart backend to clear any stuck lifecycle (semaphore=1 means one hang blocks the whole pipeline); (c) consider wrapping `_commit_question_onchain` and `_submit_to_polymarket` in `asyncio.wait_for(timeout=120s)` so PASS-path hangs convert to FAILED instead of indefinite EVALUATING. NOT a regression — pre-existing per line 180/184.
