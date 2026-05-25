# Smart Contract Invariant + Fuzz Report

## Summary
- **Invariants tested**: 5 (passed: 5 / failed: 0)
- **Fuzz tests**: 5 (passed: 5 / failed: 0)
- **Baseline regression**: 20/20 `PolyglotAlphaV2Test` tests still pass
- **New Slither findings since baseline**: 0 (counts unchanged: 9 Medium, 0 High, 22 Low, 11 Info — source files untouched)
- **Verdict**: **SAFE** (with one caveat — see Recommendations)

## Setup
- Foundry: `~/.foundry/bin/forge`, solc 0.8.35
- Test files added (no source modifications):
  - `contracts/test/Invariants.t.sol` — handler-bounded multi-contract invariants
  - `contracts/test/Fuzz.t.sol` — per-function fuzz tests
- Constructor reality vs. brief template: actual constructors are `ReputationRegistry()`, `TranslationAuction(usdc, rep)`, `BuilderFeeRouter(usdc, rep)`, `JudgePanel(usdc)`; operator is `msg.sender` at deploy time, not a constructor arg. Tests adjusted accordingly.

## Invariant results

All run with **fuzz-runs=256, depth=500 (default), ≈128 000 handler calls per invariant, 0 reverts, 0 discards.**

### #1 `invariant_stakeSumEqualsTotal` — **PASS**
- Asserts: `usdc.balanceOf(auction) >= Σ stakes(agent_i)` for the 5 bootstrap agents.
- Why `>=` not `==`: slashed funds remain in the contract treasury per README §5.6.
- 256 runs × 500 calls. No counterexample.

### #2 `invariant_reputationBounded` — **PASS**
- Asserts: `0 <= getReputation(a) <= 2.0e18` for every tracked agent.
- The EWMA recurrence `score' = 0.85·score + 0.15·signal` with `signal ∈ [0, ~2.0]` (after `_fillSignal` clamping) is a contraction toward [0, 2], so this invariant follows from the formula. Verified empirically across 128 000 mixed `updateOnAuction/Quality/Fee` + `slashReputation` paths.

### #3 `invariant_winsLeqBids` — **PASS**
- Asserts: `reps[a].totalWins <= reps[a].totalBids`.
- `updateOnAuction` only bumps `totalWins` when `won=true` AND always bumps `totalBids` first. Holds across 128 000 calls.

### #4 `invariant_feeAccrualConsistent` — **PASS**
- Asserts: `claimable(a) <= cumulativeFees(a)` for every translator.
- `recordFill` increments both by `fillAmount`; `claimFees` only zeros `claimable` (never touches `cumulativeFees`). Holds.

### #5 `invariant_registrationImpliesStake` — **PASS** (bonus)
- Asserts: for any registered agent, `lockedStakes(a) <= stakes(a)`.
- Important because `slashStake` decrements `stakes` unconditionally but only decrements `lockedStakes` if it's >= slash amount (else floored to 0). Verified the floor logic preserves the invariant across 128 000 calls including arbitrary slash sequences.

**No counterexamples found** across any invariant.

## Fuzz results

All run with `--fuzz-runs 512`.

| Function                       | Runs | Status | Notes                                                    |
|--------------------------------|------|--------|----------------------------------------------------------|
| `testFuzz_RegisterAgent`       | 512  | PASS   | Reverts iff `amount < 5_000_000`; stake = exactly 5 USDC on success |
| `testFuzz_BidWithReputation`   | 512  | PASS   | Gate is inclusive at `0.7e18`; below ⇒ `"reputation gate"` revert |
| `testFuzz_ReputationEwma`      | 512  | PASS   | Score stays ≤ 2.0e18 across up-to-79 mixed updates       |
| `testFuzz_ClaimFeesConsistent` | 512  | PASS   | `claimable` zeroed; router balance decreases by exact fill; cumulative unchanged |
| `testFuzz_SlashStake`          | 512  | PASS   | Reverts on `amount == 0` or `amount > stake`; otherwise stake decreases by exact slash |

## Slither delta
- **Baseline** (`outputs/coverage/slither-summary.txt`): 9 Medium, 22 Low, 11 Informational, 1 Optimization, 0 High
- **Current** (re-run 2026-05-26): identical — 9 Medium, 22 Low, 11 Informational, 1 Optimization, 0 High
- **New issues introduced**: 0. Source files were not modified; only test files were added.

The 9 Medium findings (6× divide-before-multiply in `_recompute` / `_fillSignal`, 3× reentrancy in `recordFill`/`claimFees`/`fund`) remain — none of them broke an invariant under stress, but they should still be triaged:
- Divide-before-multiply: `_recompute` computes `(winRate*qualityRate)/ONE` then multiplies by `fillSignal`. With 1e18 scale and counts under ~1e18 this can't underflow to zero in any reachable state we exercised, but a swap to `mulDiv` would eliminate the precision loss warning permanently.
- Reentrancy: `BuilderFeeRouter.claimFees` updates `claimable=0` *before* calling `usdc.transfer`, which is the correct CEI ordering — Slither flags it conservatively because of the external call. With MockUSDC this is fine; with a non-standard USDC token that re-enters, the invariant still holds because state is already settled.

## Recommendations

1. **Add a `ReentrancyGuard` mixin to `BuilderFeeRouter.claimFees` and `TranslationAuction.withdrawStake`** (1-line OpenZeppelin import). The current CEI ordering is correct, but a guard makes the static-analysis warning go away and defends against non-standard ERC20s (e.g. fee-on-transfer or rebasing tokens) which native USDC is *not* but a future token might be.
2. **Replace the manual fixed-point divides in `ReputationRegistry._recompute` with a `mulDiv` helper** (PRBMath or OZ Math) to silence the 6 divide-before-multiply warnings and protect future formula tweaks from precision loss.
3. **Consider documenting why `invariant_stakeSumEqualsTotal` uses `>=` not `==`** in the contract NatSpec — the slashed-stake treasury behavior is currently only described in the README, which makes the on-chain accounting confusing for auditors.
4. **Stretch goal**: add a `--fuzz-runs 5000` CI job for these invariants and a `--invariant-runs 1000 --invariant-depth 1000` deep run before any mainnet deploy. Current 256×500 is sufficient for hackathon-tier signal but not for production assurance.

---
*Generated 2026-05-26. Invariant suite: `contracts/test/Invariants.t.sol`. Fuzz suite: `contracts/test/Fuzz.t.sol`. No contract source files were modified.*
