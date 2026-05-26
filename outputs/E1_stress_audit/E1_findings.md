# E1 Findings — 20 PASS-Path Stress Audit

20/20 events SUBMITTED. No exceptions, no timeouts, no fallbacks. SSE order is stable, DB integrity is clean, winner selection is correct (20/20 vs the `min(bid/max(rep,1.0))` rule among `reputation >= 0.7` qualified bidders, with the fall-back-to-all-bidders branch exercised in V2).

Below are 4 issues found, ranked by severity.

---

## F1 [PERF, MEDIUM] Builder-fee split runs two recordFill legs serially, consuming 99% of wall-clock

**Where:** `polyglot_alpha/chain/builder_fee_router.py` `record_fill_with_split` (around lines 189-240).

**Evidence:** 6-event INFO log (`E1_orchestrator_info.log`) shows:

```
polymarket → recordFill(0.9):  median 1.12s
recordFill(0.9) → recordFill(0.1): median 1.25s
panel → split done:            median 2.39s
```

The 90% leg and the 10% leg are awaited sequentially. Total wall-clock per event is 2.4s, and 2.37s of that is the two `recordFill` Arc TXs running back-to-back. **Everything else** (auction open, 3-5 bid submits, settle, translate, evaluate, commit question, polymarket submit) takes ~30 ms combined.

**Why it matters:** 50% wall-clock reduction is on the table for free. At 20 events the saving is 24 seconds; at 1000 events it is 20 minutes.

**Constraint:** D3 owns `builder_fee_router.py` — must NOT modify per task constraints. Defer to D3.

**Recommended fix (D3):** Run both legs concurrently via `asyncio.gather`. Both already use independent nonces from `OnChainClient`. The serial pattern was likely a defensive choice for nonce ordering — but the Arc mempool accepts out-of-order nonces from the same sender, and the second-leg fee is recorded on a different `recipient_address` anyway.

---

## F2 [BUG, LOW] Treasury leg fee_amount = 0.09999999999999998 (float precision)

**Where:** `polyglot_alpha/chain/builder_fee_router.py` line 226:
```python
treasury_amount = max(_MIN_FILL_USDC, fill_amount_usdc * (1.0 - winner_share))
```
With `winner_share = 0.90`, `1.0 - 0.90 == 0.09999999999999998` in IEEE-754.

**Evidence:** Every one of 20 events persists this exact value:
```
fee_amount: 0.09999999999999998
fill_amount: 9.999999999999998   (when fill=10.0)
```
(see `audit_event_45.json` → `db_rows.builder_fee_events[1].fee_amount`).

**Why it matters:**
* The DB row is misleading for any downstream consumer that displays fees as raw `fee_amount` instead of summing.
* The sum still rounds back to exactly `1.0` because `0.9 + 0.09999999999999998 == 1.0` in float — pure luck. If `winner_share` ever becomes 0.85 or 0.75 the drift would NOT cancel and `fee_total != 1.0` invariant would break.
* The Arc on-chain `recordFill` likely takes a uint amount in micro-USDC (1e-6 base unit); the discrepancy is 2e-17 USDC — sub-base-unit. So on-chain state is unaffected, but the DB and logs show the noise.

**Constraint:** D3 owns this file — defer.

**Recommended fix (D3, < 5 lines):**
```python
TREASURY_SHARE: float = 0.10  # already a module constant
...
treasury_amount = max(_MIN_FILL_USDC, fill_amount_usdc * TREASURY_SHARE)
```
This avoids the `1.0 - 0.9` subtraction. Or quantize: `round(treasury_amount, 6)`.

---

## F3 [DESIGN, LOW] Treasury address falls back to operator wallet → 0.9 + 0.1 both go to same address in 16/20 events

**Where:** `tests/_pass_path_mocks.py` lines 290-294 (mock install):
```python
os.environ.setdefault(
    "PLATFORM_TREASURY_ADDRESS",
    os.environ.get("HACKATHON_WALLET_ADDRESS", "0x000...dead"),
)
```
AND `polyglot_alpha/orchestrator.py` `_platform_treasury_address` defaults to operator when unset.

**Evidence:** In V2-V5 the operator wallet *is also* the winner. With treasury == operator, both `recordFill` legs send to the same address. From the log:
```
recordFill(market=..., amount=0.9000, translator=0x928a7f...)
recordFill(market=..., amount=0.1000, translator=0x928a7f...)   ← same address
record_fill_with_split(... winner=0x928a7f... treasury=0x928a7f... )
```
Only V1 (4/20 events) has a distinct winner (`0xbbb...`) ≠ treasury (`0x928a...`).

**Why it matters:** The 90/10 split becomes unobservable when both addresses collapse. Any analytics/dashboard differentiating "winner earnings" from "protocol revenue" double-counts the operator. In production with a real treasury wallet this won't matter, but the audit JSONs (and any demo built off them) look like the treasury share goes to the wrong recipient.

**Constraint:** This is in `_pass_path_mocks.py` (test code, owned by A1's harness). Not blocking — flag for awareness.

**Recommended fix:** Inside `_pass_path_mocks.py`, hard-code a distinct treasury stub:
```python
os.environ.setdefault(
    "PLATFORM_TREASURY_ADDRESS",
    "0x000000000000000000000000000000000000face",  # always ≠ HACKATHON_WALLET
)
```

---

## F4 [CODE, LOW] _settle_auction's `max(reputation, 1.0)` is a no-op because reputation ∈ [0, 1]

**Where:** `polyglot_alpha/orchestrator.py` line 552:
```python
winner = min(pool, key=lambda b: b.bid_amount / max(b.reputation, 1.0))
```

**Why it matters:** `reputation` is always ≤ 1.0 in the documented schema (it's an EWMA quality score in [0, 1]). Therefore `max(reputation, 1.0) == 1.0` for every legal bid, and the divisor is always 1.0. **The reputation tier currently has zero effect on winner selection** beyond the boolean ≥0.7 qualification filter.

The intent (per the docstring at line 542) was a "soft reputation discount." Either the docstring is wrong (and reputation truly only gates qualification) or the formula should be `b.bid_amount / max(b.reputation, 0.01)` to actually advantage higher-rep bidders.

**Evidence in audit:** Computed expected winner (using the as-written formula) matches actual winner in 20/20 events — verified the formula is implemented correctly. But the rule degenerates to "lowest qualified bid wins regardless of reputation":
* V1: agent_b (bid=0.30, rep=0.92) beats agent_a (bid=0.50, rep=0.85). Bid alone explains it.
* V4 tie-break: two equal bids → `min` picks the first occurrence. Reputation 0.85 vs 0.90 is *ignored*.

**Constraint:** Orchestrator is not in the "do not touch" list. But this is a design decision, not a bug — flag for product owner.

**Recommended fix (< 5 lines, design-dependent):**
```python
# Option A: keep current behavior, fix docstring to remove "reputation discount" language
# Option B: implement the discount documented:
EFFECTIVE_REP_FLOOR = 0.5
winner = min(pool, key=lambda b: b.bid_amount / max(b.reputation, EFFECTIVE_REP_FLOOR))
```

---

## Summary

| ID | Severity | Issue | Owner |
|----|----------|-------|-------|
| F1 | MEDIUM (perf) | Serial recordFill legs → 99% of wall-clock | D3 (do not fix here) |
| F2 | LOW (precision) | `1.0 - 0.9` float artifact in treasury fee_amount | D3 (do not fix here) |
| F3 | LOW (design) | Treasury defaults to operator → 90/10 split unobservable in audits | tests/_pass_path_mocks.py |
| F4 | LOW (design) | Reputation-weighted bid score degenerates because reputation ≤ 1 | orchestrator.py (design choice) |

No fixes applied per task constraints — all four flagged for owner review. Each fix is < 5 lines.
