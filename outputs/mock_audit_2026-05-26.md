# Mock / Fake Inventory — PolyglotAlpha v2 — 2026-05-26

Read-only audit feeding the "尽量不要 mock" replacement discussion.

## Executive summary

- **Total distinct mock points found**: **31**
- **Demo-killers (🔴)**: 6
- **Credibility-loss (🟡)**: 16
- **Nice-to-have (🟢)**: 9
- **"Real" stack coverage estimate** (path actually exercised by the demo
  button today): **~25–30 %** (only LLM + corpus + DB + SSE are real;
  chain writes, Polymarket, RSS, fill stream, judge weights audit log
  are all bypassed in the default demo).

### Decisive facts established during scan

1. **Contracts ARE deployed and reachable** on Arc testnet
   (`eth_getCode` returned non-empty bytecode for all 6 contracts —
   `TranslationAuction`, `ReputationRegistry`, `BuilderFeeRouter`,
   `JudgePanel`, `QuestionRegistry`, `MockUSDC`). See verification at
   the end of this report.
2. **`polyglot_alpha/chain/` module does NOT exist** —
   `orchestrator.py` lines 139, 173, 218, 359 all `from .chain import ...`
   which always raises `ImportError`. The orchestrator therefore
   *always* runs the sha256-fake-tx-hash path, even though
   `polyglot_alpha/onchain.py` and a separate
   `polyglot_alpha/ingestion/event_dispatcher.py::ChainClient` both
   have real web3.py write logic ready to use.
3. **`.env` confirms keys are set**: `GEMINI_API_KEY`,
   `OPENROUTER_API_KEY`, `HACKATHON_WALLET_PRIVATE_KEY` are all
   present. `POLYMARKET_BUILDER_API_KEY` is empty.
   `POLYMARKET_MODE` is **not set** → defaults to `mock`.
   `DEEPSEEK_API_KEY` is **not set** → D2/D5/D7 always fall through to
   Gemini (provider diversity is partial: 3 providers in spec, 2 today).
4. **`outputs/tx_hashes.json` contains REAL Arc testnet tx hashes**
   (block 43944470+) — these came from a one-off
   `scripts/deploy_all_contracts.py` / `commitQuestion` batch, NOT from
   the lifecycle / demo button path. The data shows we *can* write to
   chain, but the orchestrator simply doesn't.

---

## Findings (sorted by priority)

### 🔴 Critical (demo-killers)

#### Mock #1 — `_open_onchain_auction` returns fake tx hash
- **File**: `/Users/messili/codebase/polyglot-alpha/polyglot_alpha/orchestrator.py:135-156`
- **Type**: Stub (ImportError fall-through) + Hardcoded
- **Current behavior**: `from .chain import auction_client` raises
  `ImportError` (module doesn't exist). Returns
  `"0x" + sha256(f"open:{event_id}:{content_hash}").hexdigest()`.
- **Real-replacement plan**:
  - **Path A** (1 h): Create `polyglot_alpha/chain/auction_client.py`
    that wraps `onchain.OnChainClient` (already implements `submit_bid`
    + `register_agent`) and adds `open_auction` calling
    `TranslationAuction.openAuction(event_id, contentHash)`. Use the
    pattern already in `ingestion/event_dispatcher.py::ChainClient`.
  - **Path B** (3 h): Full async wrapper around all four contract
    write methods (`openAuction`, `submitBid`, `settleAuction`,
    `commitQuestion`) with retry + gas estimation.
- **Cost to make real**: 1–3 h dev; gas ≈ <0.001 ETH-equivalent on Arc
  testnet per call (faucet-funded `0x928a…`).
- **Demo impact if NOT replaced**: Phase-2 "USDC Auction" card shows a
  sha256 hex that resembles a tx hash but is not on Arc explorer →
  judges click and get 404.
- **Priority**: 🔴

#### Mock #2 — `_settle_auction` returns fake tx hash
- **File**: `polyglot_alpha/orchestrator.py:199-234`
- **Type**: Stub (same ImportError fall-through)
- **Current behavior**: `"0x" + sha256(f"settle:{event_id}:{winner.agent_address}").hexdigest()`.
- **Real-replacement plan**: Same `chain/auction_client.py` —
  add `settle_auction(event_id, winner)` calling
  `TranslationAuction.settleAuction(event_id, winner)`.
- **Cost**: Bundled with Mock #1.
- **Demo impact**: Phase-2 "settlement tx" link broken.
- **Priority**: 🔴

#### Mock #3 — `_commit_question_onchain` returns fake question_id + tx hash
- **File**: `polyglot_alpha/orchestrator.py:350-378`
- **Type**: Stub
- **Current behavior**: `question_id = "0x" + sha256(...)[: 40]`,
  `tx_hash = "0x" + sha256(...).hexdigest()`. Real contract logic
  exists at `outputs/tx_hashes.json` (block 43944470+) but lifecycle
  doesn't reach it.
- **Real-replacement plan**: `polyglot_alpha/chain/question_registry.py`
  → `commit_question(event_id, candidate_hash, builder_code,
  pipeline_trace_ipfs)`. Use `QuestionRegistry.commitQuestion(...)`
  via web3.py.
- **Cost**: 1 h on top of Mock #1.
- **Demo impact**: Phase-5 "On-chain Anchor" tx hash unverifiable.
- **Priority**: 🔴

#### Mock #4 — Polymarket submission defaults to mock, builder API key empty
- **File**: `polyglot_alpha/polymarket/client.py:46-57`, `.env` line 13
  (`POLYMARKET_BUILDER_API_KEY=` empty), `polymarket/mock_client.py`
- **Type**: EnvDisabled (`POLYMARKET_MODE` unset → defaults to
  `MOCK`) + Hardcoded URLs (`gamma-api.polymarket.com/markets` only
  hit when explicitly real, returns synthetic UUID otherwise).
- **Current behavior**: `MockPolymarketClient` mints `mock-{uuid}`
  with URL `https://polymarket.com/market/mock-{uuid}` which is a
  404 on live Polymarket.
- **Real-replacement plan**:
  - **Path A** (4 h): Set `POLYMARKET_MODE=real` + register the real
    `POLYMARKET_BUILDER_API_KEY` via `polymarket.com/settings`
    builder UI. POST to Gamma `/markets`. **Risk**: any successful
    POST creates a real Polymarket market — needs sandbox / staging.
  - **Path B** (1 h, "real-but-dry"): Build the exact Gamma payload,
    log to `outputs/polymarket_payload.json`, but skip the actual POST.
    Show the payload in UI as "ready to submit, dry-run mode".
- **Cost**: $10 builder code registration if Path A; $0 for Path B.
- **Demo impact**: Phase-6 market_url is a fake 404 link unless
  Path A; Path B at least shows a real-looking payload.
- **Priority**: 🔴

#### Mock #5 — Synthetic builder-fee Poisson stream
- **File**: `polyglot_alpha/polymarket/mock_client.py:96-137` +
  `polyglot_alpha/orchestrator.py:849-872` (the orchestrator manually
  inserts ONE fake `BuilderFeeEvent` of `fill_amount=100, fee=1.0,
  arc_tx_hash="0xsimulated"` whenever `is_simulated=True`).
- **Type**: Synthetic distribution + Hardcoded constants
- **Current behavior**: 100 USD fake fill with 1.0 USD builder fee
  injected after every simulated lifecycle. `arc_tx_hash` literally
  reads `"0xsimulated"` in the DB.
- **Real-replacement plan**:
  - **Path A** (2 h): Poll `clob.polymarket.com/fills?builder_code=…`
    in real mode (already in `client.py:184-211`). Requires Mock #4
    Path A first.
  - **Path B** (free): Leave Poisson but stamp `arc_tx_hash` as
    `None` and surface "demo stream" badge in UI honestly.
- **Cost**: 2 h dev; ongoing polling 30 s per market.
- **Demo impact**: Phase-7 "Streaming Revenue" shows a literal
  `0xsimulated` string in the DB.
- **Priority**: 🔴

#### Mock #6 — UI fallback to `mock-events.json` when API errors
- **File**: `/Users/messili/codebase/polyglot-alpha/ui/lib/mock-events.json` +
  `ui/hooks/useEventList.ts:14-22` + `ui/hooks/useEvent.ts:13-15` +
  `ui/app/page.tsx:99-101` (catch → router.push fallback).
- **Type**: UIPlaceholder + Hardcoded fixtures
- **Current behavior**: 1 baked event (`evt_pboc_rrr_2026_05_25`)
  with hand-crafted bids, judges, IPFS hashes, tx hashes. If the
  backend is unreachable the UI renders this as if it were real.
- **Real-replacement plan**:
  - **Path A** (15 min): Replace fallback with an empty-state
    component ("Backend offline — start FastAPI on 8000"). No more
    silent fake data.
  - **Path B** (free): Leave fallback but flag every row with
    `mode="mock"` (already in the JSON), and have `EventCard`
    render a `MOCK` badge prominently.
- **Cost**: 15 min.
- **Demo impact**: If the backend dies mid-demo the judges still see
  data; they'll never know.
- **Priority**: 🔴

---

### 🟡 Important (credibility-loss)

#### Mock #7 — `_collect_bids` synthesizes a placeholder bid when no `mock_bids` arg passed
- **File**: `polyglot_alpha/orchestrator.py:158-196`
- **Type**: Stub + Hardcoded (`agent_address = "0x" + "a"*40`,
  `bid_amount = 1.0`, `tx_hash = "0xmockbid"`)
- **Real-replacement plan**: Run the four agents
  (`agents/runner.py --all`) which DO call
  `OnChainClient.submit_bid()` via web3.py — code already exists in
  `agents/base.py:183-205`. The demo button just needs to wait for
  real `BidSubmitted` events instead of injecting hardcoded ones.
- **Cost**: 4 h to wire end-to-end + fund 4 wallets with testnet USDC.
- **Demo impact**: Bid list in Phase-2 contains a deterministic
  `0xaaa…aaa` agent.
- **Priority**: 🟡

#### Mock #8 — Demo button injects 4 hardcoded `mock_bids`
- **File**: `/Users/messili/codebase/polyglot-alpha/ui/lib/api.ts:46-56`
- **Type**: Hardcoded test fixtures injected into prod path
- **Current behavior**: Default `triggerEvent()` payload includes
  `mock_bids: [0xgemini_agent 0.45, 0xdeepseek_agent 0.75,
  0xqwen_agent 0.55, 0xllama_agent 0.95]`. Backend uses these
  verbatim — Real agents never bid.
- **Real-replacement plan**:
  - **Path A** (30 min): Have the UI POST with `mock_bids=null`
    AND `auction_window_seconds=15`, then start the 4 agents in
    background workers (`subprocess.Popen("python -m
    polyglot_alpha.agents.runner --all")`).
  - **Path B** (15 min): Keep the 4-bid array but rename to
    `seed_bids` and surface a UI toggle "Use real agents instead".
- **Cost**: 30 min Path A; ongoing wallet funding.
- **Demo impact**: Same 4 addresses every demo; bid amounts never
  change.
- **Priority**: 🟡

#### Mock #9 — `_run_translator_pipeline` falls back to constructed P1 title when agent dispatch fails
- **File**: `polyglot_alpha/orchestrator.py:237-290`
- **Type**: Stub + Hardcoded template ("Will {title_raw} by December
  31, {year}?", `category=geopolitics`, `resolution_source=operator`)
- **Note**: `from .agents import dispatch` ImportError fall-through
  — there's no `agents/dispatch.py` module, only `agents/runner.py`.
  So this fallback ALWAYS fires.
- **Real-replacement plan**: Add `polyglot_alpha/agents/dispatch.py`
  exporting `run_for_winner(event_dict, agent_address) -> PipelineResult`.
  Real LLM calls already happen in `BaseTranslatorAgent.run_pipeline()`
  → analysts → translators → synthesizer. Just need the dispatch
  shim that maps `agent_address` → agent class and calls
  `agent.run_pipeline(event_dict)`.
- **Cost**: 2 h.
- **Demo impact**: Every demo translation reads identically: "Will X
  by December 31, 2026?" — judges will spot the pattern.
- **Priority**: 🟡

#### Mock #10 — `pipeline_trace_ipfs` is a fake `ipfs://mock/{hash}` string
- **File**: `polyglot_alpha/orchestrator.py:288`
- **Type**: Hardcoded
- **Current behavior**: `f"ipfs://mock/{candidate_hash[:12]}"`. Not a
  real IPFS CID, not pinned anywhere.
- **Real-replacement plan**:
  - **Path A** (2 h): Pin pipeline trace JSON to web3.storage (free
    tier, 1 TB) → real `ipfs://bafy…` CID.
  - **Path B** (15 min): Stop emitting an IPFS URL entirely; store
    the trace JSON in SQLite + serve via `/events/{id}/trace`.
- **Cost**: 2 h Path A (free API key); 15 min Path B.
- **Demo impact**: Phase-3 "trace" link 404s.
- **Priority**: 🟡

#### Mock #11 — Polymarket market_id `sim-{uuid}` when client missing
- **File**: `polyglot_alpha/orchestrator.py:441-447`
- **Type**: Hardcoded
- **Current behavior**: `market_id = f"sim-{uuid.uuid4().hex[:12]}"`
  if Polymarket import errors. Mirror of Mock #4.
- **Real-replacement plan**: Bundle with Mock #4.
- **Priority**: 🟡

#### Mock #12 — Builder-fee event hardcoded `arc_tx_hash="0xsimulated"`
- **File**: `polyglot_alpha/orchestrator.py:859`
- **Type**: Hardcoded sentinel
- **Real-replacement plan**: Call
  `BuilderFeeRouter.recordFill(market_id, amount, translator)` —
  contract is deployed at `0xcE7596…d150e5`, `fill_listener.py`
  already has the real `ChainRecorder` impl. Just need the
  orchestrator's synthetic event to use the real recorder.
- **Cost**: 1 h.
- **Priority**: 🟡

#### Mock #13 — `_evaluate_with_judges` mock verdict when panel import fails
- **File**: `polyglot_alpha/orchestrator.py:293-347`
- **Type**: Stub + Hardcoded (`{"judge_i": 0.85}`,
  `style_judge_i: True`)
- **Current behavior**: If `from .judges import panel` fails or
  raises, the orchestrator fabricates `8 × 0.85 + 3 × True = PASS`.
  The real panel IS importable so this only fires on exceptions —
  but `RuntimeError|ValueError|KeyError|HTTPError` all swallow.
- **Real-replacement plan**: Remove the mock fallback. Let the
  exception surface → `EventStatus.FAILED` instead of fake PASS.
- **Cost**: 15 min.
- **Priority**: 🟡

#### Mock #14 — BLEU judge skipped with neutral 0.5 when no reference
- **File**: `polyglot_alpha/judges/translation/bleu_judge.py:42-52`
- **Type**: Synthetic neutral
- **Current behavior**: `passed=True, score=0.5` whenever
  `reference_translation` is None. The demo NEVER supplies a
  reference (it's not in `TriggerRequest`).
- **Real-replacement plan**: Load reference from
  `outputs/reference_translations.jsonl` (file exists) keyed by
  source title hash. Already curated for the 5 sample events.
- **Cost**: 30 min.
- **Demo impact**: BLEU contributes 10% of the weighted score
  unconditionally — judges will notice if asked.
- **Priority**: 🟡

#### Mock #15 — COMET judge neutral-passes when model unavailable
- **File**: `polyglot_alpha/judges/translation/comet_judge.py:159-166`
- **Type**: Stub (graceful degradation)
- **Current behavior**: `passed=True, score=0.5` when COMET model
  can't be downloaded. COMET 2.2.7 + Python 3.14 has known compat
  issues (see lines 11-17 of the file).
- **Real-replacement plan**: Pre-download
  `Unbabel/wmt22-cometkiwi-da` (gated; requires HF token + license
  acceptance) OR `Unbabel/wmt20-comet-qe-da` (free) into
  `~/.cache/torch/comet/` ahead of demo. CI can run
  `scripts/test_comet.py` to verify.
- **Cost**: 30 min + ~500 MB disk.
- **Priority**: 🟡

#### Mock #16 — MQM judge offline-pass when LLM unreachable
- **File**: `polyglot_alpha/judges/translation/mqm_llm_judge.py:243-259`
- **Type**: Stub (graceful degradation)
- **Real-replacement plan**: `OPENROUTER_API_KEY` is set in `.env`
  → MQM should run real. Confirm by running 1 lifecycle with
  `tail -f outputs/llm_cost_log.jsonl` and looking for
  `success=true, provider=openrouter:meta-llama/llama-3.3-70b-instruct`
  lines.
- **Cost**: free if key already works.
- **Priority**: 🟡

#### Mock #17 — D2/D3/D5/D6/D7 style judges fall back to neutral pass when no LLM key
- **File**: `polyglot_alpha/judges/style_alignment/llm_batch.py:276-289`
  + each of `d2/d3/d6/d7/d5_resolution_clarity.py`
- **Type**: Stub (graceful degradation when no DEEPSEEK / OPENROUTER /
  GEMINI key)
- **Current behavior**: `passed=True, score=0.5, offline=True` for
  the dimension. Provider diversity is incomplete since
  `DEEPSEEK_API_KEY` is unset (D2/D5/D7 → Gemini fallback, not
  DeepSeek as documented in `llm_batch.PROVIDER_FOR_DIMENSION`).
- **Real-replacement plan**: Register DeepSeek API key
  ($1 free credit available) → restores 3-provider diversity. Audit
  via `grep '"offline": true' outputs/llm_cost_log.jsonl`.
- **Cost**: free; 15 min signup.
- **Priority**: 🟡

#### Mock #18 — D1 structural judge LLM fallback returns LOW confidence (0.6)
- **File**: `polyglot_alpha/judges/style_alignment/d1_structural.py:188-221`
- **Type**: Stub (LLM fallback when regex misses)
- **Current behavior**: When LLM fallback succeeds, confidence is
  capped at 0.6 (`LLM_FALLBACK_CONFIDENCE`). Not strictly mock —
  this is by design — but the demo's hardcoded "Will X by December
  31, 2026?" titles always pass the regex, so the LLM fallback never
  fires anyway.
- **Real-replacement plan**: Track. Not urgent unless demo titles
  diverge from P1.
- **Priority**: 🟡

#### Mock #19 — `compute_content_hash` uses TITLE not body, dedup ignores actual content
- **File**: `polyglot_alpha/orchestrator.py:557-568`
- **Type**: Hardcoded shape
- **Current behavior**: Hash is over `{title, sources, language}`
  only — body of the news event is NOT in the hash. Two genuinely
  different news items with the same title dedup as duplicates.
- **Real-replacement plan**: Add `body` / `summary` to the payload.
  Beware breaks existing dedup; consider versioning the hash.
- **Cost**: 15 min + DB migration considerations.
- **Priority**: 🟡

#### Mock #20 — `events.py` hardcodes `"mode": "mock"` for every event
- **File**: `polyglot_alpha/api/routes/events.py:65`
- **Type**: Hardcoded UI label
- **Current behavior**: Every event returned to the UI carries
  `"mode": "mock"` regardless of whether the lifecycle ran with
  real bids / real chain / real Polymarket. UI shows "MOCK" badge
  on real runs too.
- **Real-replacement plan**: Compute from `submission.is_simulated`
  (already exists in `PolymarketSubmission`):
  `mode = "live" if submission and not submission.is_simulated
  else "mock"`.
- **Cost**: 5 min.
- **Demo impact**: Even after fixing Mock #4, the UI still labels
  the event "mock".
- **Priority**: 🟡

#### Mock #21 — Agents history `reputation` field is synthetic linear function of count
- **File**: `polyglot_alpha/api/routes/agents.py:58, 68`
- **Type**: Synthetic
- **Current behavior**: `"reputation": min(1.0, 0.5 + 0.05 * (len(points) + 1))`
  — i.e. reputation in history view is a linear ramp 0.55, 0.60,
  0.65, … not the real time-series of `AgentReputation.avg_quality`.
- **Real-replacement plan**: Snapshot real `avg_quality` after each
  win → store in a new `AgentReputationHistory` table. Or, for the
  hackathon, just report the current `avg_quality` for every point
  (constant line is honest; ramp is a lie).
- **Cost**: 30 min minimal fix; 2 h proper.
- **Priority**: 🟡

#### Mock #22 — `_hardcoded_chinese_samples` 5 baked PBOC/Xinhua titles
- **File**: `polyglot_alpha/ingestion/event_dispatcher.py:245-266`
- **Type**: Hardcoded fixtures used when sample_*.json missing
- **Current behavior**: If `outputs/sample_*.json` doesn't exist
  (it DOES — 5 files), fallback emits 5 baked PBOC/Xinhua titles
  with `http://www.pbc.gov.cn/` etc as sources.
- **Real-replacement plan**: Have RSS aggregator
  (`ingestion/rss_aggregator.py` — real, polls Caixin/Xinhua/SCMP/
  Le Monde/etc) actually produce events into `outputs/`. Currently
  it's never run as a background process.
- **Cost**: 2 h to wire as systemd / launchd service.
- **Priority**: 🟡

---

### 🟢 Nice-to-have

#### Mock #23 — `event_dispatcher.run_demo` with `use_chain=False` default
- **File**: `polyglot_alpha/ingestion/event_dispatcher.py:269-289, 322-326`
- **Type**: DryRun flag
- **Current behavior**: CLI `--use-chain` defaults to False; says
  "defaults to dry run".
- **Real-replacement plan**: Flip default to True now that contracts
  are deployed. Or remove the flag entirely.
- **Priority**: 🟢

#### Mock #24 — `MockLLM` deterministic canned response
- **File**: `polyglot_alpha/llm.py:167-185`
- **Type**: Stub for tests
- **Current behavior**: Returns hardcoded JSON about "Mock market
  question for tests?". Only fires when keys are missing (they aren't
  in `.env`) OR `make_llm(mock=True)`. Safe in prod.
- **Real-replacement plan**: Leave as-is for tests. Audit that
  prod paths never construct with `mock=True`.
- **Priority**: 🟢

#### Mock #25 — `BaseTranslatorAgent.evaluate_event` confidence heuristic
- **File**: `polyglot_alpha/agents/base.py:113-132`
- **Type**: Hardcoded magic numbers (`body_len / 4000`, `0.5 baseline`,
  `expected_cost_usdc = 0.05 + body_len / 8000`)
- **Real-replacement plan**: Actually estimate token cost from
  upstream LLM provider pricing tables.
- **Priority**: 🟢

#### Mock #26 — `D1 PATTERN_PRIORS` claims "corpus-derived" but hardcoded as constants
- **File**: `polyglot_alpha/judges/style_alignment/d1_structural.py:46-53`
- **Type**: Hardcoded (derived FROM a real scan, but the numbers
  themselves are baked in code)
- **Real-replacement plan**: Compute at import-time from
  `corpus/patterns_report.md`. Cosmetic; the values won't change
  between demos.
- **Priority**: 🟢

#### Mock #27 — Quality eval magic thresholds (`30, 12, 0.7`)
- **File**: `polyglot_alpha/quality_eval.py:13-15`
- **Type**: Hardcoded thresholds (file docstring even labels itself
  "stub")
- **Real-replacement plan**: Drop this entirely once the 11-judge
  panel is wired (it is). The agent self-eval is duplicate work.
- **Priority**: 🟢

#### Mock #28 — Builder-code derivation uses sha256 truncated to 10 chars
- **File**: `polyglot_alpha/polymarket/builder_code.py:75-82`
- **Type**: Hardcoded derivation (when no real code provided)
- **Real-replacement plan**: Once Polymarket builder is registered
  (Mock #4 Path A), use the real code. Until then, hash is fine.
- **Priority**: 🟢

#### Mock #29 — `outputs/builder_codes.json` is the "registry"
- **File**: `polyglot_alpha/polymarket/builder_code.py:35-36`
- **Type**: File-based fake registry (not a real Polymarket lookup)
- **Real-replacement plan**: Bundle with Mock #4.
- **Priority**: 🟢

#### Mock #30 — Auction history reputation step `0.05 * (n+1)`
- Already covered in Mock #21.

#### Mock #31 — `MockUSDC.sol` used by test suite — confirmed test-only
- **File**: `contracts/test/MockUSDC.sol` (under `test/`, not `src/`)
- **Type**: Test fixture only
- **Note**: Even though `MockUSDC` is also deployed on Arc testnet
  at `0x477f…391D` (it's listed in `.env`), the contract is
  intentionally not under `src/`. On a real chain you'd point at the
  USDC contract. For Arc testnet, no real USDC exists so the mock
  IS the production token. Document this explicitly.
- **Real-replacement plan**: Acknowledge in §5.30 honest scope.
- **Priority**: 🟢

---

## Replacement plan by category

### Category 1: LLM / AI layer
- **Status**: Mostly real! Gemini + OpenRouter keys present →
  analysts, translators, synthesizer, MQM judge, D1 LLM fallback,
  D2-D7 batch ALL call real LLMs when reached.
- **But**: Reach is gated by Mock #9 (agent dispatch module missing).
  Currently every demo synthesizes the same P1 template title without
  invoking analysts/translators/synthesizer.
- **Total mock items here**: 5 (#9, #14, #15, #16, #17, #18)
- **Effort to fix**: ~3 h (write `agents/dispatch.py` + register
  DeepSeek + preload COMET).
- **$ cost**: $0 (free tiers) + $0.10 per real lifecycle (Gemini
  free tier covers, OpenRouter pay-per-token).
- **Blockers**: None.

### Category 2: Chain layer (Arc testnet)
- **Status**: Contracts DEPLOYED and reachable; web3 client code
  EXISTS in `onchain.py` and `ingestion/event_dispatcher.py`; but
  orchestrator does NOT call them. ENTIRELY mock in the demo path.
- **Total mock items**: 6 (#1, #2, #3, #7, #10, #12)
- **Effort to fix**: ~4 h (create `polyglot_alpha/chain/` package
  with `auction_client.py`, `question_registry.py`,
  `builder_fee_router.py` shims).
- **$ cost**: $0 (Arc testnet, hackathon wallet funded already per
  `deployer=0x928a…` in `outputs/deployment_v2.json`).
- **Blockers**: Need to confirm hackathon wallet has enough Arc
  testnet ETH for ~7 tx per lifecycle.

### Category 3: Polymarket layer
- **Status**: Defaults to mock. Real client code exists but routes
  to `MockPolymarketClient` because `POLYMARKET_MODE` unset.
- **Total mock items**: 4 (#4, #5, #11, #28/#29)
- **Effort to fix (Path A real)**: ~6 h + builder registration.
- **Effort to fix (Path B dry)**: ~1 h (just build + log the real
  payload, skip submit).
- **$ cost Path A**: $10 builder cap + ongoing fills.
- **$ cost Path B**: $0.
- **Recommendation**: Path B for the hackathon — submitting real
  unverified markets is reputationally risky and the judges will
  understand "dry-run: payload built but not submitted" better than a
  fake `mock-{uuid}` URL.

### Category 4: Data sources (RSS / corpus)
- **Status**: RSS aggregator (`rss_aggregator.py`) is REAL — polls 8+
  feeds (`sources.json`). Corpus scraper (`scraper.py`,
  `full_scraper.py`) is REAL — populates
  `corpus/polymarket_index.faiss` from gamma-api.polymarket.com.
  FAISS index IS loaded in D8 judge.
- **But**: RSS aggregator never runs in the demo path. Demo button
  uses hardcoded title from UI `api.ts`.
- **Total mock items**: 3 (#8 demo bids + title hardcoded, #19
  content_hash misses body, #22 fallback samples)
- **Effort to fix**: 2 h (cron the RSS aggregator + DB read in
  `/trigger/event` to pull the latest unprocessed event instead of
  accepting a hardcoded title).
- **$ cost**: $0.
- **Blockers**: None.

### Category 5: UI layer
- **Status**: Real wiring through `lib/api.ts` + `useEventList`.
  Falls back to `mock-events.json` only on backend error.
- **Total mock items**: 3 (#6 fallback file, #8 default payload,
  #20 hardcoded "mock" mode label)
- **Effort to fix**: 1 h.
- **$ cost**: $0.

### Category 6: Tests vs production blurring
- **Cleanest in the codebase**. Production code does NOT branch on
  pytest markers. Mocks live in:
  - `polyglot_alpha/polymarket/mock_client.py` (used by real client
    as fallback, NOT a test fixture).
  - `polyglot_alpha/llm.py:MockLLM` (used when env key missing).
  - `contracts/test/MockUSDC.sol` (under test/, but ALSO deployed
    to Arc testnet as the production token — Arc has no real USDC).
- No other test-vs-prod blurring detected.

---

## Quick wins (under 1 h each)

| # | Fix | Time |
|---|-----|------|
| #20 | events.py hardcoded `mode="mock"` → compute from `is_simulated` | 5 min |
| #13 | Remove orchestrator's mock judge-verdict fallback (let it fail) | 15 min |
| #6 | UI: replace `mock-events.json` fallback with empty state | 15 min |
| #14 | Load real reference from `outputs/reference_translations.jsonl` | 30 min |
| #10 | Replace `ipfs://mock/…` with `/events/{id}/trace` route | 30 min |
| #17 | Register DEEPSEEK_API_KEY → restores 3-provider diversity | 15 min |
| #21 | Agents history: stop ramping reputation linearly | 30 min |

Total quick-win effort: **~2.5 h** for 7 mocks (mostly 🟡).

## Hard wins (4 h+, high credibility ROI)

| # | Fix | Time |
|---|-----|------|
| #1, #2, #3, #7, #10, #12 | Create `polyglot_alpha/chain/` package — real Arc TX everywhere | 4–6 h |
| #9 | Add `agents/dispatch.py` shim → real 4-LLM translator pipeline | 2 h |
| #8 | UI: drop `mock_bids` default, run 4 real agents in background | 4 h |
| #4 (Path B) | Polymarket dry-run: build real Gamma payload, log, no POST | 2 h |
| #5, #12 | Wire `BuilderFeeRouter.recordFill` via existing `ChainRecorder` | 2 h |

Total hard-win effort: **~14–18 h** for the remaining critical/🟡
mocks. Knocking out all of these moves the demo from ~25% real →
~85% real.

## Defer to production (acknowledge in §5.30 honest scope)

- **#4 Path A** (real Polymarket submission). Posting real prediction
  markets from a hackathon project is reputationally and legally
  risky; explicitly out of scope.
- **#18** D1 LLM-fallback confidence cap (by design).
- **#28, #29** Builder-code registry as JSON file (real registration
  happens during the production rollout).
- **#31** MockUSDC on Arc testnet (Arc testnet has no real USDC; the
  mock IS the canonical token for testnet — document explicitly).

## Recommended sequence

### Day 1 (4 h) — Real chain layer
- Create `polyglot_alpha/chain/{auction_client,question_registry,builder_fee_router}.py`
  thin shims that wrap `OnChainClient` (already implemented).
- Patch `orchestrator.py` lines 139, 173, 218, 359 to use them.
- Drop Mock #20 (UI mode label) — 5 min.
- Drop Mock #13 (mock judge fallback) — 15 min.
- Verify: `outputs/tx_hashes.json` grows by ~7 entries per demo
  click; Arc Explorer shows real txs.
- **Result**: Mocks #1, #2, #3, #7, #12, #13, #20 closed. Demo phase 2,
  5, 7 cards link to real Arc Explorer URLs.

### Day 2 (8 h) — Real translator pipeline + real RSS + Polymarket dry-run
- Add `agents/dispatch.py` shim → real 4-LLM pipeline on demo button
  (~2 h). Closes Mock #9.
- Wire RSS aggregator → DB and have `/trigger/event` accept an
  optional `event_id` from latest unprocessed RSS hit (~2 h).
  Closes Mock #22, partial #8.
- Polymarket dry-run mode (`POLYMARKET_MODE=dry`): build the exact
  Gamma payload, log to `outputs/polymarket_payloads/`, skip submit
  (~2 h). Closes Mock #4 / #5 / #11 honestly.
- Drop UI `mock_bids` default from `triggerEvent()`; toggle in UI
  to "use seed bids" only for offline demos (~1 h). Closes Mock #8.
- Register DEEPSEEK_API_KEY (15 min). Closes Mock #17.
- Load reference translations into BLEU judge (30 min). Closes Mock #14.

### Day 3 (4 h) — Polish + UI honesty
- UI fallback: replace `mock-events.json` with empty state (~30 min).
  Closes Mock #6.
- IPFS pin OR drop fake IPFS URLs in favour of `/events/{id}/trace`
  endpoint (~30 min). Closes Mock #10.
- Agents history reputation: serve real `avg_quality` snapshots
  (~30 min). Closes Mock #21.
- Verify demo button → all 7 phases write real artifacts.

**Total: ~16 h** to flip demo from YELLOW → GREEN-leaning.

---

## On-chain verification (already done, included for the record)

```
TranslationAuction:  0xE046Ea8478855A653bAdc9Fbd12ae4B8A429907a  12129 B  deployed=True
ReputationRegistry:  0x00267FD2FFabDDB48bBF16e3a91C15DE260eF9F1   6394 B  deployed=True
BuilderFeeRouter:    0xcE7596d9b21333Eae441E912699514F6fBD150e5   7609 B  deployed=True
JudgePanel:          0x1eE7BADc48b52B36e086adb4a98E00cbff4efd9a   7643 B  deployed=True
QuestionRegistry:    0x9b7D81064E76E6E70e238A6EA361A9E2da2a81B1   4294 B  deployed=True
MockUSDC:            0x477fC4C3DcC87C3Ceb13adc931F6bBeDAcCa391D   3436 B  deployed=True
```

(via `eth_getCode` against `https://rpc.testnet.arc.network`, 2026-05-26.)

`outputs/tx_hashes.json` confirms `commitQuestion` has worked from a
one-off script — block 43944470+, gas 213k-231k per call. The
mechanism works; the orchestrator just needs to call it.
