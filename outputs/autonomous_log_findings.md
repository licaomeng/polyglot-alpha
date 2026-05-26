# T2 Autonomous Log + DB Monitoring Findings

Agent: sub-agent T2
Window: 2026-05-26 ~11:54 → ~12:35 PDT-08 (local UTC+8)
Backend log: `/tmp/polyglot_backend_new.log` (latest of the `polyglot_backend*.log` family)
DB: `/Users/messili/codebase/polyglot-alpha/polyglot_alpha.db`

This file is updated incrementally; final summary at bottom.

---

## 1. Initial baseline (12:00 local)

DB state (events table):

| status      | count |
|-------------|-------|
| FAILED      | 11    |
| REJECTED    | 8     |
| TRANSLATING | 1     |

- Highest `id` = 20 (REJECTED, triggered at 03:54 UTC = 11:54 local).
- Bid count per recent event: **3/3** — auction layer is healthy.
- `builder_fee_events` table: **0 rows**, `is_simulated` always 0 historically. No `0xsimulated` strings present anywhere. F2 (simulated-tx fix) holds.
- All 8 ranked quality verdicts in `quality_scores` are `FAIL`. Zero `PASS` so far.

## 2. Error-pattern inventory (cumulative on `polyglot_backend_new.log`, ~560–800 lines)

Sorted by frequency at the start of the monitoring window:

| count | pattern                                                          | severity | notes |
|-------|------------------------------------------------------------------|----------|-------|
| 183   | `GET /events/156` → 404                                          | LOW (UI) | A browser tab is polling a non-existent event_id=156 every ~4s (`useEvent.refetchInterval=4000`). Backend handles it fine — pure UI cruft. No fix needed for demo. |
| 3     | `panel.evaluate: judge=… timed out after 60s`                    | LOW      | d8_duplicate_detection (2x) and comet (1x). Soft-skip path converts them to `passed=True`. Not blocking. |
| 3     | `Retrying request to /v1/messages`                                | LOW      | Anthropic SDK normal retry behaviour; no 429 yet. |
| 1     | `synthesizer: LLM HTTP call failed (model=claude-haiku-4-5-…): Connection error.` | LOW | Heuristic fallback kicks in (`synthesizer.py:106`). Note the log line prints `event_id=` empty — minor cosmetic logging bug. |
| 1     | `COMET predict failed (Unbabel/wmt20-comet-qe-da): not enough values to unpack (expected 3, got 2)` | MED | `model.predict` unpack mismatch on first warm-load. Try/except handles it gracefully → judge returns `None`. Likely a `unbabel-comet` / `pytorch_lightning` version drift. |
| 0     | `Traceback`, `RuntimeError`, `KeyError`, `AttributeError`, `ValueError` | —    | Clean. |
| 1     | `insufficient funds` (gemini, event 21)                          | **HIGH** | F1 regression — see §6. `nonce too low` still 0. |
| 0     | `429 Too Many Requests`                                          | —        | Semaphore=5 is working. |
| 0     | `Event loop is closed`                                           | —        | F3 holds. |
| 0     | `0xsimulated`                                                    | —        | F2 holds. |
| 0     | HTTP 5xx (`HTTP/1.1" 5xx`)                                       | —        | No backend 500s. |
| 0     | `Lifecycle … FAILED`                                             | —        | No lifecycle-level failures. |

Of the 472 HTTP-coded lines in the log, **0 are 5xx, 193 are 404 (the `/events/156` UI poll), 206 are 200**. Backend HTTP health is clean.

## 3. HIGH severity finding: every translation gets `verdict=FAIL`

This is the **dominant failure mode** for the demo. `quality_scores` table:

```
event 20 overall_score=0.82 verdict=FAIL
event 19 overall_score=0.74 verdict=FAIL
event 18 overall_score=0.74 verdict=FAIL
event 12 overall_score=0.74 verdict=FAIL
event 11 overall_score=0.7  verdict=FAIL
event  9 overall_score=0.69 verdict=FAIL
event  2 overall_score=0.74 verdict=FAIL
event  1 overall_score=0.74 verdict=FAIL
```

Style-alignment passes (`d1`…`d8`) for the same 5 events:

```
event 20: {"d1":true,"d2":false,"d3":true,"d4":true,"d5":false,"d6":false,"d7":false,"d8":true}
event 19: {"d1":true,"d2":true, "d3":true,"d4":true,"d5":false,"d6":false,"d7":false,"d8":true}
event 18: {"d1":true,"d2":true, "d3":true,"d4":true,"d5":false,"d6":false,"d7":false,"d8":true}
event 12: {"d1":true,"d2":true, "d3":true,"d4":true,"d5":false,"d6":false,"d7":false,"d8":true}
event 11: {"d1":true,"d2":true, "d3":true,"d4":true,"d5":false,"d6":false,"d7":false,"d8":true}
```

`HARD_STYLE_REQUIREMENTS = ("d1", "d5", "d8")` (`judges/types.py:32`). **`d5_resolution_clarity` is FALSE on every single event**, which makes `hard_pass=False` and therefore `overall_pass=False`, regardless of how well MQM/COMET/etc. score.

### Root-cause trace

In `polyglot_alpha/judges/style_alignment/d5_resolution_clarity.py`:

- The **rule-based fast path** correctly PASSES (cutoff_ts parses, resolution_criteria has YES/NO axis). Verified manually for event 20 — `cutoff_ts="2026-08-23T00:00:00Z"`, `resolution_criteria="This market resolves YES if the PBOC officially announces a reduction to the Res…"`.
- The fast path falls through to the **LLM slow path** because `ANTHROPIC_API_KEY` is set in the env.
- The LLM tier returns one or more "ambiguities" → `passed=False` (lines 297–314).

So the demo is failing at the LLM ambiguity-flagger, which is being **too strict on well-formed translations** that the rule path already considers valid. Note that `d6_source_reliability` and `d7_leading_check` are also FALSE on every event — they have similar LLM-critique structure and likely fail in the same way.

### Recommended fix (NOT applied — out of scope for "small fix")

Two reasonable approaches; both >2 lines:

1. **Trust the rule path more.** In `d5_resolution_clarity.py`, only flip rule-PASS to FAIL when the LLM flags ≥2 ambiguities (or all of them are "major"). One-line change to line 280–298 but requires semantic call.
2. **Loosen `HARD_STYLE_REQUIREMENTS`** to just `("d1", "d8")` so the LLM critic dimensions only contribute to the majority gate, not the hard gate. One-line change in `judges/types.py:32` — but conceptually weakens the demo's claimed quality bar.

Either change has product implications, so I am documenting rather than applying.

## 4. MEDIUM finding: COMET first-call unpack error

`COMET predict failed (Unbabel/wmt20-comet-qe-da): not enough values to unpack (expected 3, got 2)` fires once on the first warm-load. After that COMET works (subsequent panel runs do not log this). The except block in `comet_judge.py:122–124` swallows it gracefully — the judge returns `None`, BLEU passes by default, MQM=100, so the translation gate would still pass on this dimension.

Not blocking, but it does mean COMET silently no-ops on the *first* event after backend start. If T1 / the demo only triggers once, COMET evidence will be `null`.

## 4.5. LOW finding: event 13 is stuck in TRANSLATING since 11:05 (>1h)

`SELECT id, status, triggered_at FROM events WHERE status='TRANSLATING' OR status='EVALUATING' OR status='AUCTIONING'` returns:

```
13 | TRANSLATING | 2026-05-26 03:05:26.567149   (UTC, = 11:05 local)
```

Event 13 began translation over an hour ago and never moved to EVALUATING / REJECTED / PASS. No `stuck`, `recovery`, or `reaper` symbol is referenced in `orchestrator.py`, so there is no automatic stale-state cleanup. This is a dormant inconsistency in the DB but does not affect the demo (it just shows as a permanent "TRANSLATING" row in the events list). Operator can `UPDATE events SET status='FAILED' WHERE id=13` if the UI clutter matters; not applying because user said no DB writes.

## 5. LOW finding: `synthesizer` log line has empty `event_id=`

`synthesizer.py:106` formats `event_id=%s` with `event.event_id`, which is empty string at the synthesis stage (the event has not yet been persisted with an id). Cosmetic — doesn't affect the pipeline. Could be fixed by passing the trigger's event_id down through `synthesize_question` if desired; not blocking demo.

## 6. HIGH severity finding (LIVE during monitoring): gemini seeder ETH balance drained → "insufficient funds for gas" → only 2/3 bidders on event 21

At 12:04:39 event 21 triggered. Backend log:

```
agent=deepseek bid=0.8000 USDC tx=5d15510b…
agent=gemini  submit_bid failed: {'code': -32003, 'message':
   'insufficient funds for gas * price + value:
    have 4922303472678425  want 5000731791250000'}
agent=qwen    bid=0.7500 USDC tx=a6a16e14…
real-auction: 2/3 agents bid successfully
```

Have 4.92e15 wei (≈0.00492 ETH), need 5.00e15 wei. Off by ~0.00008 ETH.

This contradicts the "F1: seeders funded" guarantee — funding decays as agents
spend gas on each bid + settle cycle. The auction tolerated 2/3 and continued,
so event 21 still settled (qwen won) and reached the panel, but if **two**
agents drain below 0.005 ETH the auction will fall under quorum.

### FIX APPLIED (12:06)

Ran the **idempotent** top-up script:

```
.venv/bin/python scripts/faucet_agents.py
```

Pre-faucet balances (just before refill):
| agent     | ETH       | USDC |
|-----------|-----------|------|
| gemini    | 0.004922  | 20.0 |
| deepseek  | 0.035691  | 20.0 |
| qwen      | 0.013578  | 20.0 |

Post-faucet: all three at **eth=0.050000, usdc=20.0**. Receipts:
- gemini  eth_tx=0x2d89ca434a67984b8d7e930ac7cbedb09cfa448aa473bcb5dc4d7a9c6634bc45
- deepseek eth_tx=0xddee75d1fabcd3b29dec764578321dc35dd43e01891ddae6d52d12a2dfb4bce1
- qwen    eth_tx=0xb91fb3c65a76ff68672c315b3cca60a73aea0dc2c9ab20d588c522e61e284d50

This restores 3/3 auctions for the next ~10-20 events. The underlying issue
(no auto-faucet inside the long-running backend) is **not** fixed; consider
running `scripts/faucet_agents.py` from a `while true; sleep 600; …` external
process during long demos, or wiring an in-process top-up into the auction
trigger path.

## 6.5. MEDIUM finding: `collect_bids_inline` always returns 0 bids → falls back to legacy auction path on every event

Log shows **7 occurrences** (every auction) of:

```
dispatch.collect_bids_inline returned 0 bids; falling back to legacy real-auction path
```

Yet **no** `agent task crashed` messages appear, even though `_safe_agent_bid` propagates exceptions and the orchestrator logs them on line 500 of `agents/dispatch.py`. This means tasks did not raise — they just didn't *complete* in time. Reading `collect_bids_inline`:

```python
done, pending = await asyncio.wait(
    tasks,
    timeout=max(window_seconds, 0.0),  # 30s default
    return_when=asyncio.ALL_COMPLETED,
)
```

The 30 s window covers each agent's LLM-driven `evaluate_event` + on-chain `placeBid`. Per backend log, individual Anthropic round-trips are 5-15 s; with three agents serialized on the operator's nonce lock, the combined deadline is easy to miss.

**Effect**: the inline path is dead code in practice. The legacy real-auction path picks up the slack and does succeed (3/3 bids when agents are funded). So the system still produces bids — just through the fallback. No demo impact, but the "fast inline auction" code is currently always being skipped.

**Suggested fix (NOT applied, larger than 2 lines)**: raise `_DEFAULT_AUCTION_WINDOW_SECONDS` from 30 to 90 in `polyglot_alpha/agents/dispatch.py:48`, OR change the orchestrator condition so the inline path only triggers in tests / when no operator PK is set.

## 6.6. LOW finding: transient `Connection reset by peer` on Arc RPC

```
agent=qwen submit_bid failed: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))
```

Single occurrence on event 25. Auction tolerated it (2/3 bids) and proceeded. Indicates Arc devnet RPC occasionally drops the TCP connection. No retry layer in the auction path — if this hit two agents simultaneously the auction would have only 1 bidder. Worth adding a retry-with-backoff around `submit_bid`, but it's a rare event.

## 6.7. HIGH severity finding (LIVE): event 27 reached PASS but is stuck in EVALUATING

This is the **first PASS verdict** of the entire monitoring window — and the pipeline does not advance past it.

```
quality_scores.event_id=27  overall_score=0.74  verdict=PASS  evaluated_at=04:21:36 UTC
events.id=27                status=EVALUATING (still, as of 04:26:30 UTC, ≈5 min later)
```

After `panel.evaluate: collected 8/11 judges` for event 27, the orchestrator was supposed to (`orchestrator.py:1566`):

1. `_commit_question_onchain` → mark event COMMITTED + emit `onchain.committed` event.
2. `_submit_to_polymarket` → mark event SUBMITTED.

Neither happened. Log has **zero** mentions of `commit_question`, `registerQuestion`, `onchain.committed`, or `polymarket.*submit` for event 27. The backend is healthy (`GET /health` still 200), so the orchestrator coroutine is blocked inside `await question_registry.commit_question(...)`.

This is the **demo-blocking bug**: on the rare event that the panel verdict is PASS, the chain commit hangs and the event never reaches SUBMITTED. The user will see "EVALUATING" forever in the UI.

### Probable root cause (not verified)

`OnChainClient.sign_and_send` (referenced in `dispatch.py:411` comment as "serialising nonces") may be holding a lock awaiting a tx receipt that the Arc devnet RPC never returns. The `Connection reset by peer` error earlier on event 25 suggests RPC instability — if `commit_question` hit the same flake but lacks the retry/timeout the `submit_bid` path has, it will hang indefinitely.

### Recommended fix (NOT applied)

Add a `asyncio.wait_for(question_registry.commit_question(...), timeout=60)` around the call in `orchestrator.py:1566`, mirroring the panel.evaluate per-judge timeout pattern, and on timeout return the `pending-{event_id}` sentinel from the existing `except` branches. This is a 3-4 line change. I'm leaving it for the user to apply after the monitoring window.

## 7. State evolution timeline (live)

Filled in by the background `Monitor` task as events arrive. See section 9 (final summary) below.

Event arrivals during monitoring window:
- 12:03:39 baseline — last event = 20 (REJECTED), 11 FAILED + 8 REJECTED + 1 TRANSLATING
- 12:04:39 event 21 → EVALUATING; gemini bid failed (insufficient funds, 2/3 quorum)
- 12:05:39 event 21 → REJECTED (score=0.75, FAIL via d5)
- **12:06    faucet run — all 3 agents restored to 0.05 ETH**
- 12:07:39 event 22 → 3/3 bids → REJECTED (0.74, d5)
- 12:09:40 event 23 → 3/3 bids → REJECTED (0.74, d5)
- 12:11:40 event 24 → 3/3 bids → REJECTED (0.85, d5; highest score yet still FAIL)
- 12:13:40 event 25 → 2/3 bids (qwen RPC reset) → REJECTED (FAIL via d5)
- 12:15:40 event 26 → 3/3 bids → REJECTED
- 12:17:40 event 27 → 3/3 bids → **PASS (0.74)** → ⚠️ STUCK IN EVALUATING (chain commit hang)
- 12:30:41 event 28 → PENDING (event 27 still stuck in EVALUATING — 9+ min after PASS verdict, hang confirmed durable)

Final DB state (12:27):

```
status       | count          verdict | count
EVALUATING   | 1   (event 27) FAIL    | 14
FAILED       | 11               PASS    | 1   (event 27, stuck)
REJECTED     | 14
TRANSLATING  | 1   (event 13, >1h stale)
```

## 8. Summary table — top error categories by frequency

Final cumulative counts on `/tmp/polyglot_backend_new.log` (which grew from 520 → ~1750 lines over the window):

| rank | error | count | severity | status |
|------|-------|-------|----------|--------|
| 1 | `GET /events/156` → 404 (browser tab on stale event_id) | ~600+ | LOW (UI) | left alone (out of scope) |
| 2 | `panel.evaluate: judge=…  timed out after 60s` | 14 | LOW | mostly d8/comet (soft-skipped) + 1× d7 (hard FAIL) |
| 3 | `dispatch.collect_bids_inline returned 0 bids; falling back to legacy real-auction path` | 7+ | MEDIUM | every auction falls back; legacy works (see §6.5) |
| 4 | `Retrying request to /v1/messages` (Anthropic SDK) | ~6 | LOW | benign |
| 5 | `agent=gemini submit_bid failed: insufficient funds` | 1 | HIGH (fixed) | resolved by faucet at 12:06 |
| 5 | `Connection reset by peer` (Arc RPC) | 1 | LOW | transient, auction quorum tolerated |
| 6 | `synthesizer: LLM HTTP call failed … Connection error` | 1 | LOW | heuristic fallback works |
| 6 | `COMET predict failed (… not enough values to unpack (expected 3, got 2))` | 1 | LOW | first-warm error, judge returns None |

## 9. Fixes applied during monitoring window

1. **`scripts/faucet_agents.py` re-run at 12:06** — restored all 3 agents to eth=0.05, usdc=20.0. Receipts in §6. Effect: gemini stopped failing bids; 3/3 quorum on events 22-27.

NO code edits, NO commits, NO backend restarts performed.

## 10. Outstanding bugs (severity-sorted)

| sev | bug | location | recommended fix |
|-----|-----|----------|------------------|
| **HIGH** | Event 27 (PASS) stuck in EVALUATING — chain commit hang | `orchestrator.py:1566` → `_commit_question_onchain` → `question_registry.commit_question` | wrap in `asyncio.wait_for(..., timeout=60)` and fall through to `pending-{event_id}` sentinel on timeout |
| **HIGH** | d5/d6/d7 LLM critique judges fail every event (100% rejection rate when chain commit doesn't hang) | `judges/style_alignment/d5_resolution_clarity.py:280-314` | trust rule-path PASS unless LLM flags ≥2 ambiguities; OR drop d5/d6/d7 from HARD_STYLE_REQUIREMENTS for demo |
| HIGH (latent) | Agent wallets drain to <0.005 ETH after ~5-10 events | seeder agents consume gas with no auto-faucet | run `scripts/faucet_agents.py` from cron every 10 min during demo |
| MEDIUM | `collect_bids_inline` always times out at 30s → dead-code path | `polyglot_alpha/agents/dispatch.py:48` | raise `_DEFAULT_AUCTION_WINDOW_SECONDS` to 90 |
| MEDIUM | COMET first-call unpack error (library version drift) | `judges/translation/comet_judge.py:121` | pin `unbabel-comet` / `pytorch_lightning` versions; or pre-warm the model on backend startup |
| LOW | Arc RPC occasionally `Connection reset by peer` | `dispatch.py:_safe_agent_bid` | wrap `submit_bid` in retry-with-backoff (≤2 retries, 1s+2s) |
| LOW | Event 13 stuck in TRANSLATING since 11:05 (>1h) | no recovery scheduler | add a startup cleanup that flips events in non-terminal states older than 5 min to FAILED |
| LOW | `synthesizer` log line prints `event_id=` empty | `synthesizer.py:106` | pass the event id explicitly into `synthesize_question` |
| LOW (UI) | Browser tab polling `/events/156` (does not exist) generates ~10 404s/min | `ui/hooks/useEvent.ts:12` (`refetchInterval: 4000`) | not a backend bug — close the stale tab |

## 11. Final recommendation

**NOT demo-ready yet — fix at least the chain-commit hang (HIGH-1) before recording the final video.** Pipeline currently gets 1/7 PASS in this window, and even the lone PASS hangs in EVALUATING for >5 min because the on-chain commit never returns. Until that timeout/retry is added (or the Arc RPC behaviour is fixed), the demo will show "EVALUATING…" indefinitely on the happy path.

If you also want a green pass-rate for the demo, additionally relax d5/d6/d7 in the hard style gate (HIGH-2). Without that, expect ~85-95% REJECT rate.

The auction layer + funding (post-faucet) + judges-other-than-d5 are healthy.
