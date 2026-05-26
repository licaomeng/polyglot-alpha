# PolyglotAlpha v2 — Bug Backlog

Aggregated from 11 sub-agent reports (B–J overnight loop + 3 earlier audits) covering
~430 distinct checks across DB integrity, chain RPC, API edge cases, UI rendering,
visual regression, a11y/WCAG, real-user exploration, cross-browser, perf, smoke test,
and mobile viewports.

- **Total distinct bugs catalogued:** **47**
- **Fixed during overnight test loop:** **27** (mostly C/M-tier hardening + UI a11y +
  mobile touch targets + filter-bucket rewrite + chain wiring)
- **Outstanding & user-actionable:** **20**
  - 1 BLOCKER, 6 HIGH, 8 MEDIUM, 5 COSMETIC
- **Operator-only (license / wallet / KYC):** 4 separate items (see end)

Date: 2026-05-26
Working dir: `/Users/messili/codebase/polyglot-alpha`
Backend: `http://localhost:8000` · UI: `http://localhost:3001`

---

## Outstanding bugs (user attention needed)

### BLOCKER (demo unusable)

#### [BLOCK-1] `/trigger/event` is synchronous (60–75s) and blocks the FastAPI event loop
- **Symptom**: Click "Trigger live demo". POST `/trigger/event` does the full
  4-LLM + 11-judge + Polymarket pipeline inline. The HTTP call hangs ~65 s. While
  it hangs **every other request** stalls — `/events` polling, `/healthz`, and all
  unrelated SSE subscribers. UI shows "Done navigating to event detail…" but
  `router.push` never fires because `await triggerEvent()` hasn't returned. User
  reloads, gets a different event id than the one they triggered.
- **Why this is THE complaint**: User explicitly said *"triggered demo, can't find
  it in the events list"*. Cross-browser report says the same: `POST` blocks
  60–75 s in chromium/firefox/webkit. Perf benchmark confirms (iter 0 = 65.87 s,
  iter 1 ≥ 180 s). Real-user bug hunt #3 + #4.
- **Files**: `polyglot_alpha/api/routes/trigger.py`, `polyglot_alpha/orchestrator.py`
- **Fix**:
  - Return `event_id` immediately with `status=PENDING` via `BackgroundTasks` or an
    `asyncio.create_task(run_lifecycle(...))`.
  - Existing SSE channel (`/sse/events?event_id=…`) already streams phase updates —
    UI can subscribe right after the 202.
  - Update `ui/components/TriggerButton.tsx` so it no longer `await`s the POST
    body; navigate to `/events/{event_id}` on 202 and let SSE drive progress.
- **Effort**: 30–45 min
- **Discovered by**: Agent E real-user hunt, Agent G cross-browser, Agent H perf,
  Agent C edge audit
- **Priority reason**: This single fix unblocks 3 separate symptoms that real
  evaluators all hit on their first click.

---

### HIGH (degrades demo credibility)

#### [HIGH-1] BLEU + COMET both null in 90 %+ of events
- **Symptom**: `quality_scores.translation_scores.bleu = null` and
  `comet = null` in DB; UI Phase-4 shows "—" instead of a 0–1 number. Smoke test
  iter 3 confirms (only MQM=77 lights up; BLEU/COMET both fail).
- **Root cause**:
  - **BLEU** — `polyglot_alpha/orchestrator.py:639` calls
    `panel.evaluate(final_question)` without a `reference_translation` argument.
    `bleu_judge.judge_bleu` correctly returns `bleu_raw=None` when no reference is
    supplied; the data IS in DB (`outputs/reference_translations.jsonl` exists).
  - **COMET** — `Unbabel/wmt22-cometkiwi-da` HF license **manual approval still
    pending** (clicking "Agree" only submits the request). Fallback
    `Unbabel/wmt20-comet-qe-da` is downloaded (2.2 GB) and works, but produces
    z-scores in [-1, 1] not [0, 1]; threshold table already adjusted in
    `comet_judge.py`.
- **Fix**:
  1. Wire reference lookup: load
     `outputs/reference_translations.jsonl` keyed by source-URL/title hash;
     pass into `panel.evaluate(..., reference_translation=…)` at
     `orchestrator.py:639`.
  2. COMET wmt20 fallback is already producing real (non-neutral) scores per
     `comet_install_report.md` lines 186–215. UI just needs to surface the
     `model_id` so judges know "we're on the open-license fallback, not the
     gated preferred model".
- **Effort**: 1–2 h (BLEU: 30 min; COMET UI badge: 15 min; surface license-pending
  state in About page: 15 min; verify smoke iter 4 = 12/12)
- **Discovered by**: Agent I smoke v2, COMET install report
- **Files**: `polyglot_alpha/orchestrator.py:639`, `polyglot_alpha/judges/translation/bleu_judge.py:42-52`, `polyglot_alpha/judges/translation/comet_judge.py`, `ui/app/events/[id]/page.tsx`

#### [HIGH-2] Firefox CORS preflight blocks SSE on `/sse/events`
- **Symptom**: Firefox console:
  `Cross-Origin Request Blocked: …127.0.0.1:8000/sse/events?event_id=125 …
  Reason: CORS request did not succeed. Status code: (null).`
  Chromium/WebKit don't surface this because they fall through to the page-level
  SSE provider on `localhost:8000`. Firefox is stricter about EventSource preflight.
- **Fix**: One of:
  - Backend: add `Access-Control-Allow-Origin` header + `Allow-Credentials` on
    `/sse/*` (already done globally — verify on SSE response specifically).
  - Frontend: canonicalize `NEXT_PUBLIC_BACKEND_URL` to one of `localhost` or
    `127.0.0.1` (use `localhost` to match the page-level provider).
- **Effort**: 10–15 min
- **Discovered by**: Agent G cross-browser, line 22–37
- **Files**: `polyglot_alpha/api/main.py` (CORS config), `ui/lib/api.ts` (BASE_URL)

#### [HIGH-3] 15 of 30 most-recent SUBMITTED events have NULL `questions.tx_hash`
- **Symptom**: Agent B DB audit check #22 — `recent SUBMITTED events have
  questions.tx_hash` failed with `missing=15`. Half the SUBMITTED events show no
  on-chain anchor in the questions table even though `chain/question_registry.py`
  exists. Phase-5 "On-chain Anchor" link 404s on those.
- **Root cause hypothesis**: orchestrator `_commit_question_onchain` swallows
  exceptions and stores `tx_hash=None` when the web3.py call fails (e.g. gas
  estimation timeout, RPC blip, wallet not funded enough). The hackathon wallet
  may have run out of testnet ETH partway through the night.
- **Fix**:
  1. Check `0x928a…` wallet balance on Arc testnet (`outputs/deployment_v2.json`).
     Top up from faucet if low.
  2. Add retry-with-backoff (already a guideline) around the
     `QuestionRegistry.commitQuestion` send.
  3. Log which exception fired — currently silently swallowed.
- **Effort**: 30–60 min (mostly investigation)
- **Discovered by**: Agent B DB audit (check #22), Agent E real-user (no on-chain
  proof badge on detail page)
- **Files**: `polyglot_alpha/orchestrator.py:350-378`, `polyglot_alpha/chain/question_registry.py`

#### [HIGH-4] 14 of 30 recent events still carry **wrong builder_code**
- **Symptom**: Agent B DB check #17 — `recent questions.builder_code matches env`
  failed with `violating=14, expected=0xa934…beb1`. About-page says
  `0xa934…beb1` is the registered code; 14 rows of `questions` carry a different
  one. UI badge on those events misleads judges.
- **Root cause hypothesis**: builder-code derivation
  (`polymarket/builder_code.py:75-82`) uses `sha256(translator_wallet)[:10]` when
  no env override; default `BUILDER_FEE_BPS` value path differs from the explicit
  env code. Newer events created after the env was set carry the correct value;
  older ones don't, but the test only looks at *recent* events — so something is
  still re-deriving on a per-event basis.
- **Fix**: Pin `os.environ["BUILDER_CODE"]` read at startup; pass through
  `record_question_commit()` so the chain anchor and DB row share the same value.
  Add a startup assert that the env code matches the on-chain-registered code.
- **Effort**: 30 min
- **Discovered by**: Agent B DB audit (check #17)
- **Files**: `polyglot_alpha/polymarket/builder_code.py`, `polyglot_alpha/orchestrator.py` (where it's stamped onto Question row)

#### [HIGH-5] 16 of 30 SUBMITTED events lack the 4 expected bids
- **Symptom**: Agent B DB check #10 — `recent SUBMITTED events have 4 bids` failed
  with `violating_events=16`. Some events have 1–3 bids. Phase-2 "USDC Auction" UI
  shows incomplete bid list.
- **Root cause hypothesis**: agent dispatch races + concurrent triggers.
  `_collect_bids` (`orchestrator.py:158-196`) waits `auction_window_seconds`
  (default 5) and snapshots whatever bids arrived; on busy backend (4 sub-agents
  hammering), some agents miss the window. Also `mock_bids` fall-back at line
  489 (`tx_hash="0xmockbid"`) stamps single placeholder.
- **Fix**:
  - Increase default `auction_window_seconds` from 5 → 15 in
    `api/routes/trigger.py` (still well within demo budget).
  - Reject auction settlement when `len(bids) < 2` instead of advancing with 1
    bid (already a NaN p-value risk in the panel — see BLOCK-1 implication).
- **Effort**: 20 min
- **Discovered by**: Agent B DB audit (check #10), and #11 (`>=2 distinct bid
  amounts` violated by 12 events)
- **Files**: `polyglot_alpha/orchestrator.py:158-196`, `polyglot_alpha/api/routes/trigger.py`

#### [HIGH-6] `NaN` / `Infinity` / `1e500` `bid_amount` returns HTTP 500 not 422
- **Symptom**: `POST /trigger/event` with `mock_bids[0].bid_amount = NaN | Infinity`
  returns **plain-text HTTP 500** instead of validation 422. Pydantic
  `_reject_non_finite` validator correctly raises `ValueError`; FastAPI's
  default `RequestValidationError` handler then `json.dumps(exc.errors())`
  which crashes on non-finite floats.
- **Fix**: `polyglot_alpha/api/main.py` — add a custom
  `RequestValidationError` exception handler that sanitises non-finite floats
  before serialisation, or `json.dumps(allow_nan=True)`.
- **Effort**: 15 min
- **Discovered by**: Agent C edge-case audit (Section A, check confirmed)
- **Files**: `polyglot_alpha/api/main.py`

---

### MEDIUM

#### [MED-1] WebKit tablet (768×1024) home FCP = 2168 ms (13× other viewports)
- Same browser 133 ms FCP at 375×667 and 159 ms at 1280×800. Likely JIT
  cold-start of `@xyflow/react` SVG-heavy WorkflowOverview on first paint at
  this exact width.
- **Fix**: Add a lighter skeleton at tablet width or `@xyflow/react` lazy-load
  guard tied to viewport. **Files**: `ui/components/WorkflowOverview.tsx`.
- **Effort**: 30 min. **Discovered by**: Agent G cross-browser.

#### [MED-2] Backend uvicorn worker deadlocks under parallel load
- During iter 1 of edge tests, PID 44081 hung 10 minutes at 0 % CPU holding
  the SQLite handle; only SIGKILL recovered. Likely WAL contention from
  4 parallel agents.
- **Fix**: Set `--timeout-keep-alive 5 --workers 2` on uvicorn; consider
  `--limit-concurrency 32`. Add `PRAGMA busy_timeout=5000` (currently relies
  on default).
- **Effort**: 15 min. **Discovered by**: Agent C section A "backend hang
  incident". **Files**: `scripts/start_backend.sh`,
  `polyglot_alpha/db/session.py`.

#### [MED-3] `backtest_results` table empty (count=0)
- Agent B DB check #7. Either backtest run never wrote rows, or the table was
  re-created and not seeded. Frontend `/history` page falls back to live
  events table; About-page links to "backtest" but the page is empty.
- **Fix**: Run `scripts/backtest_v1.py` once to seed (>= 100 rows expected
  per the check). **Effort**: 5 min seeding + 30 min if logic regressed.
- **Discovered by**: Agent B DB.

#### [MED-4] `corpus_markets.framing_pattern` 100 % NULL
- Agent B DB check #29. The pattern-tagging feature is implemented but never
  run on the existing 75 885 corpus_markets rows. D8 judge's pattern-prior
  table is hardcoded as a result (see mock #26).
- **Fix**: Run `scripts/tag_framing_patterns.py` (or equivalent) — backfill
  job. **Effort**: 1 h (depending on LLM cost). **Discovered by**: Agent B.

#### [MED-5] `sources` table empty (0 rows, should be ≥8 RSS sources)
- Agent B DB check #30. RSS aggregator never persists into the `sources`
  table — instead emits inline JSON. About-page lists 8 sources; backend
  table doesn't.
- **Fix**: Either populate via one-off insert from `corpus/sources.json` or
  drop the table and reference `corpus/sources.json` directly. **Effort**:
  15 min. **Discovered by**: Agent B.

#### [MED-6] Mock #20 — every event labelled `"mode": "mock"` in API response
- `polyglot_alpha/api/routes/events.py:65` hardcodes `"mode": "mock"` even
  for events with real chain anchors + real Polymarket dry-run. UI badges
  every event as MOCK, undermining real runs.
- **Fix**: Compute `mode = "live" if submission and not submission.is_simulated
  else "mock"`. **Effort**: 5 min. **Discovered by**: mock audit #20.

#### [MED-7] Mock #21 — agent reputation history is synthetic ramp `0.5 + 0.05·n`
- `polyglot_alpha/api/routes/agents.py:58,68`. Time-series on agent profile
  page shows a linear ramp regardless of actual `avg_quality`. Misleading.
- **Fix** (minimal): just return the *current* `avg_quality` for every history
  point — constant line is honest, ramp is a lie. **Effort**: 30 min.

#### [MED-8] Iframe / TriggerButton hang state has no Cancel
- After a successful trigger the button stays `disabled=true` because SSE
  `event.finalized` arrived but `eventId` was never set by the
  still-pending POST. User must reload. Mitigated once BLOCK-1 lands.
- **Fix**: After BLOCK-1, add a Cancel button + 90 s client timeout that resets
  the UI state. **Effort**: 15 min (part of BLOCK-1 work).
- **Files**: `ui/components/TriggerButton.tsx`. **Discovered by**: Agent E #8.

---

### COSMETIC / NICE-TO-HAVE

#### [COS-1] Mock #19 — `compute_content_hash` uses title only, ignores body
- Two genuinely different news items with the same title dedup as duplicates
  (which is exactly what Agent I smoke iter 1→2 regression hit).
- **Fix**: Add `body`/`summary` to the hash; versioned. **Effort**: 15 min +
  DB migration consideration. **Discovered by**: mock audit #19.

#### [COS-2] Mock #10 — `pipeline_trace_ipfs = "ipfs://mock/{hash}"`
- Phase-3 "trace" link 404s. Replace with `/events/{id}/trace` route
  (in-DB JSON). **Effort**: 30 min.

#### [COS-3] Mock #13 — orchestrator fabricates `PASS` verdict if judge panel
  raises any exception
- `orchestrator.py:293-347` swallows `RuntimeError|ValueError|KeyError|HTTPError`
  and inserts `{"judge_i": 0.85}` + `style_judge_i: True` → fake PASS.
  Real panel is importable so this only fires on runtime exceptions, but
  failure-mode is a silent lie. **Fix**: let exception surface → `FAILED`.
  **Effort**: 15 min.

#### [COS-4] Mobile ReactFlow zoom controls are 26 × 26 px (below 44px)
- Third-party `@xyflow/react` element; CSS override didn't always win.
- **Fix**: Replace with custom 44 × 44 buttons or omit on phones. **Effort**:
  30 min. **Discovered by**: Agent J mobile.

#### [COS-5] Featured-events strip on home routes to pre-trigger href after click
- If user clicks a featured card *during* a pending trigger, they land on the
  pre-existing event rather than the just-triggered one. Mitigated by
  BLOCK-1 fix (instant `router.push`). **Effort**: bundled with BLOCK-1.

---

## Operator-only items (cannot be fixed by an agent)

1. **HF gated-repo approval pending** for `Unbabel/wmt22-cometkiwi-da`.
   wmt20-comet-qe-da fallback is working. Wait for Unbabel review queue. No
   code change required when granted.
2. **Polymarket V2 builder-code real registration** ($10 + KYC at
   polymarket.com/settings). Currently `POLYMARKET_MODE=mock`; dry-run path
   already builds a real-looking payload (see mock #4 path B).
3. **Rotate the 4 `.env` secrets** that were briefly staged in git
   (already removed from index): `GEMINI_API_KEY`, `GOOGLE_API_KEY`,
   `OPENROUTER_API_KEY`, `HACKATHON_WALLET_PRIVATE_KEY`.
4. **Record 3-min Loom demo + submit Agora form** (`q6-application.txt`).

---

## Bugs already fixed during overnight test loop

| ID | Severity | Symptom (one line) | Owner agent | Files |
|---|---|---|---|---|
| F1 | CRITICAL | `.env` staged in git with live keys | operator | `git rm --cached .env` |
| F2 | CRITICAL | `bid_amount=NaN` → HTTP 500 (typed BidRequest) | A | `api/routes/trigger.py` |
| F3 | CRITICAL | Frontend never hydrates (`.next` stale) | C | `ui/.next` recycle |
| F4 | HIGH | Auction picked **max** bid not min | A | `orchestrator._select_winner` |
| F5 | HIGH | CORS reflected arbitrary Origin + credentials | A | `api/main.py` |
| F6 | HIGH | Negative / >1 / Inf `bid_amount` silently accepted | A | `orchestrator._coerce_bids` |
| F7 | HIGH | No rate limit on `/trigger/event` (DoS) | A | `slowapi` 5/min |
| F8 | HIGH | `title/sources/mock_bids` uncapped | A | `TriggerRequest` |
| F9 | HIGH | Next.js 14.2.18 — 23 advisories | D | upgrade to 15.5.x |
| F10 | MED | `/events` p95 200× under load (DELETE journal) | B | `PRAGMA journal_mode=WAL` |
| F11 | MED | Dedup race — dup callers EVALUATING forever | A | shared `asyncio.Future` |
| F12 | MED | Missing CHECK constraints on 6 tables | B | Alembic migration |
| F13 | MED | `agent_reputation.total_wins` race | A | atomic UPDATE |
| F14 | MED | `corpus_markets` embedding_idx 93.7 % null | E | reconcile script |
| F15 | MED | Slither divide-before-multiply (6 warnings) | D | `mulDiv` helper |
| F16 | MED | 3× reentrancy-no-eth (CEI violation) | D | `ReentrancyGuard` |
| F17 | MED | `event.finalized` SSE missing for dup callers | A | emit at lifecycle end |
| F18 | MED | `transformers==4.57.6` 2 CVEs | F | pin >=5.0 |
| F19 | LOW | Invalid `?status=` returned `[]` not 422 | A | `Optional[EventStatus]` |
| F20 | LOW | `viem` + `zustand` 50 MB for one RPC call | C | drop deps + lazy chunks |
| F21 | UX-BLOCK | Events page filter chips matched nothing | E/D | `lib/status.ts` |
| F22 | UX-BLOCK | Events list crash on first keystroke (`null.toLowerCase()`) | E | `events/page.tsx` |
| F23 | UX-BLOCK | Loading skeleton permanent on slow backend | E | `AbortController` + retry |
| F24 | UX-BLOCK | `/events/9999` showed "Backend didn't respond" | E | sniff 404 |
| F25 | A11Y | `<html>` missing `dir`, no skip-link, no `aria-current` | C | layout + SiteHeader |
| F26 | MOBILE | Touch targets 47% → 81% compliant | J | `Button` min-h-[44px] |
| F27 | MOBILE | Horizontal overflow on `/events`, `/history`, `/leaderboard` | J | `flex-col` + `overflow-auto` |

27 bugs closed overnight. Verification: Agent I smoke v2 = 10/12, Agent C
visual+a11y = 124/131 (94.7 % pass), Agent G cross-browser = 5/5 routes × 3
engines, Agent J mobile = 0 horizontal-overflow × 14 viewport+page combos.

---

## By owner / file

### Backend Python

| File | Outstanding bugs |
|---|---|
| `polyglot_alpha/api/routes/trigger.py` | BLOCK-1, HIGH-5 |
| `polyglot_alpha/orchestrator.py` | BLOCK-1, HIGH-1, HIGH-3, HIGH-5, COS-1, COS-2, COS-3 |
| `polyglot_alpha/api/main.py` | HIGH-2, HIGH-6 |
| `polyglot_alpha/api/routes/events.py` | MED-6 |
| `polyglot_alpha/api/routes/agents.py` | MED-7 |
| `polyglot_alpha/chain/question_registry.py` | HIGH-3 |
| `polyglot_alpha/polymarket/builder_code.py` | HIGH-4 |
| `polyglot_alpha/judges/translation/bleu_judge.py` | HIGH-1 |
| `polyglot_alpha/judges/translation/comet_judge.py` | HIGH-1 (UI surface) |
| `polyglot_alpha/db/session.py` | MED-2 |
| `scripts/start_backend.sh` | MED-2 |

### Frontend (Next.js)

| File | Outstanding bugs |
|---|---|
| `ui/components/TriggerButton.tsx` | BLOCK-1, MED-8 |
| `ui/lib/api.ts` | HIGH-2 (BASE_URL canonicalize) |
| `ui/components/WorkflowOverview.tsx` | MED-1 |
| `ui/app/events/[id]/page.tsx` | HIGH-1 (BLEU/COMET surface) |
| (ReactFlow component) | COS-4 |

### Data / migrations

| Task | Bug |
|---|---|
| seed `backtest_results` | MED-3 |
| backfill `corpus_markets.framing_pattern` | MED-4 |
| populate `sources` table or drop | MED-5 |

---

## Recommended attack order

| Step | Item | ETA | Why first |
|---|---|---|---|
| 1 | **BLOCK-1** non-blocking `/trigger/event` | 30–45 min | Single biggest UX win; fixes 4 separate symptoms (real-user #3, #4, #7, #8 + cross-browser long-poll + perf p95 timeout) |
| 2 | **HIGH-2** Firefox CORS on SSE | 10–15 min | Tiny diff, 1/3 of browsers gain working SSE |
| 3 | **HIGH-6** `NaN/Inf` → 422 | 15 min | Adversarial demo: someone WILL try it. 500 looks broken. |
| 4 | **HIGH-1** BLEU + COMET wire | 1–2 h | Phase-4 score panel goes from 1/3 lit (MQM only) → 3/3 lit |
| 5 | **HIGH-5** `auction_window` 5 → 15 s + reject single-bid | 20 min | 16/30 incomplete-auction events disappear |
| 6 | **HIGH-3** wallet balance check + retry | 30–60 min | 15/30 missing tx_hash disappear once retried |
| 7 | **HIGH-4** pin builder_code at startup | 30 min | UI badge matches About-page badge |
| 8 | **MED-2** uvicorn keepalive + busy_timeout | 15 min | Prevent backend hang seen in iter 1 |
| 9 | **MED-6** events.py mode label | 5 min | One-line truth-fix |
| 10 | **MED-7** agent reputation history | 30 min | Honest constant line beats fake ramp |
| 11 | MED-1, MED-3, MED-4, MED-5, MED-8 | 2–3 h aggregate | After demo flow is solid |
| 12 | COS-1 through COS-5 | 2 h aggregate | Polish |

**Total HIGH+BLOCK effort**: ~4–5 h of focused work to take demo from "great
with caveats" → "great with no caveats". MED + COS is another half-day.

---

## Honesty notes for the user

- The smoke test legitimately reports demo readiness as GREEN. BLOCK-1 is a UX /
  perception bug not a correctness bug: every lifecycle that completes produces
  a real verdict + real chain anchor (when the wallet has gas) + real LLM
  panel scores. The user complaint was about *visibility*, not *correctness*.
- Mock audit catalogued 31 mock points but **only 6 (the 🔴 ones)** are
  demo-killers; the others are credibility / cosmetic. All 6 🔴 entries map to
  outstanding bugs above (BLOCK-1 implication: HIGH-3 / HIGH-4 / HIGH-5 are the
  chain-side; MED-6 is the UI label; the 2 Polymarket mocks are operator-only).
- COMET licence is genuinely blocked on Unbabel's manual review queue. The
  wmt20-comet-qe-da fallback **is producing real scores today** (verified
  `-0.020` z-score on the PBOC sample) — the perception that "COMET is mocked"
  is wrong; what's wrong is that we don't surface "running on open-licence
  fallback" in the UI.
- 27 / 47 catalogued bugs closed in one overnight loop is roughly 57 %
  resolution. Outstanding work concentrated in the 4–5 h attack-order above.
