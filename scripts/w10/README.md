# W10 verification harness — pre-built scripts

The four scripts below are the W10-PREP deliverables: they let four W10
sub-agents kick off in parallel the moment W9-A (JudgePanel attestation)
and W9-E (final wiring) land. **Do not run these scripts before W9-A and
W9-E are merged** — the chain-consistency sweep relies on the
`events.judges_attestation_tx` column W9-A introduces, and the UI
regression / stress audit scripts assume the W9-A & W9-E UX surfaces are
present on `/events/*`.

All four scripts are idempotent + safe to re-run.

---

## 1. `scripts/w10/chain_consistency_sweep.py`

**Purpose.** Sweep the latest N `SUBMITTED` events through
`scripts/verify_chain_consistency.py` (NOT modified — wrapped) and
aggregate the per-phase PASS/FAIL/SKIP counts. For every FAIL the
verifier diff is included verbatim plus a heuristic "root cause" line.

**Invocation:**

```bash
.venv/bin/python scripts/w10/chain_consistency_sweep.py             # 20 events, any mode
.venv/bin/python scripts/w10/chain_consistency_sweep.py --limit 30 --mode live
.venv/bin/python scripts/w10/chain_consistency_sweep.py --mode mock --out /tmp/w10-chain-mock.md
```

**Output:** `/tmp/w10-chain-sweep.md` (override with `--out`).
**Console:** per-phase tally summary at the end.
**Exit code:** 0 if every checked phase is PASS, 1 otherwise.

**Expected runtime:** ~6–10 s per event (most of that is RPC). 20 events
→ ~2–3 min on the Arc testnet RPC.

**Dependencies:** Python venv at `.venv/`, `web3` (already pinned in
`pyproject.toml`), `.env` populated with the contract addresses
(`TRANSLATION_AUCTION_ADDRESS`, `JUDGE_PANEL_ADDRESS`, …). All present.

---

## 2. `ui/scripts/w10/ui_regression_sweep.mjs`

**Purpose.** Re-verify the 13 W3-regression points (from
`scripts/wave3_regression.mjs`) against the current UI in BOTH `mode=live`
and `mode=mock`. Aggregates console.errors / 4xx-5xx / 429 across both
runs.

**Invocation:**

```bash
node ui/scripts/w10/ui_regression_sweep.mjs
# override target events:
W10_EVENT_LIVE=214 W10_EVENT_MOCK=213 node ui/scripts/w10/ui_regression_sweep.mjs
```

**Output:** `/tmp/w10-ui-regression.md` (per-fix PASS/FAIL matrix +
console / network sections).
**Exit code:** 0 if every check passes in both modes, 1 if any fails.

**Expected runtime:** ~90–120 s (13 checks × 2 modes × page navigation).

**Dependencies:** UI on `:3001` and API on `:8000`. Playwright + Chromium
already installed under `ui/node_modules`. No new deps.

**Sample matrix row (expected when clean):**

```
| R1 | Phase 4 judge panel renders 11 judges | PASS | PASS |
| R9 | SSE rate-limit — 5 rapid reloads → 0 × 429 | PASS | PASS |
```

---

## 3. `scripts/w10/test_suite_runner.sh`

**Purpose.** Run pytest + jest + `tsc --noEmit` one after the other,
tail-trim each log into `/tmp`, then print a summary block.

**Invocation:**

```bash
bash scripts/w10/test_suite_runner.sh
bash scripts/w10/test_suite_runner.sh --no-jest          # skip UI suite
bash scripts/w10/test_suite_runner.sh --no-pytest        # skip backend
```

**Outputs:**

- `/tmp/w10-pytest.log` — last 80 lines of pytest stdout
- `/tmp/w10-jest.log` — last 60 lines of jest output
- `/tmp/w10-tsc.log` — last 40 lines of `tsc --noEmit`
- `/tmp/w10-test-suite.summary` — distilled pass/fail counts

**Exit code:** 0 if all three suites pass and `tsc` reports 0 errors;
non-zero otherwise.

**Expected runtime:** pytest ≈ 60–90 s, jest ≈ 30–45 s, tsc ≈ 20–30 s →
total ≈ 2–3 min.

**Dependencies:** `.venv/bin/python` (fallback: `python3`),
`ui/node_modules/.bin/jest` and `tsc` (already installed).

---

## 4. `ui/scripts/w10/concurrent_stress_audit.mjs`

**Purpose.** Trigger N mock + M live events in parallel, wait for each to
reach a terminal status, then run `scripts/verify_chain_consistency.py`
on each. Checks three system-wide invariants:

| # | invariant |
|---|-----------|
| I1 | leaderboard NOT polluted by mock-only placeholder addrs |
| I2 | every live event has a real on-chain trace (no `0xsim_…`) |
| I3 | SSE never emits a 429 under load |

**Invocation:**

```bash
node ui/scripts/w10/concurrent_stress_audit.mjs --mock 5 --live 3
node ui/scripts/w10/concurrent_stress_audit.mjs --mock 5 --live 0   # mock-only dry-run
```

**Output:** `/tmp/w10-stress-audit.md` (per-scenario table + invariants
+ raw trigger response bodies for debugging).

**Exit code:** 0 if every scenario AND every invariant pass; 1 on any
violation; 2 on fatal error.

**Expected runtime:** mock terminal ≈ 15–25 s; live terminal ≈ 60–90 s on
Arc testnet. Wall-clock ≈ 2–3 min for 5+3 because triggers fire in
parallel.

**Dependencies:** Backend at `:8000` + UI at `:3001` + Arc testnet
funded faucet keys in `.env` (live triggers consume gas). Playwright
+ Chromium already installed.

---

## Pre-flight checklist

Before any W10 sub-agent runs these scripts, confirm:

- [ ] backend up: `curl -fs http://localhost:8000/health` returns 200
- [ ] UI up: `curl -fs http://localhost:3001/` returns 200
- [ ] DB writable: `ls polyglot_alpha.db*` (WAL/SHM files OK)
- [ ] `.env` has `TRANSLATION_AUCTION_ADDRESS`, `QUESTION_REGISTRY_ADDRESS`,
      `BUILDER_FEE_ROUTER_ADDRESS`, `REPUTATION_REGISTRY_ADDRESS`,
      `JUDGE_PANEL_ADDRESS`
- [ ] Arc faucet keys in `.env` if `--live N` with `N > 0`
- [ ] W9-A column `events.judges_attestation_tx` present
      (`sqlite3 polyglot_alpha.db "PRAGMA table_info(events)" | grep judges`)
- [ ] W9-E surfaces deployed (judge panel + reputation widgets on `/events/*`)

---

## How the four sub-agents map onto these scripts

| W10 sub-agent | script | output to consume |
|---------------|--------|-------------------|
| chain-consistency | `scripts/w10/chain_consistency_sweep.py --limit 20` | `/tmp/w10-chain-sweep.md` |
| ui-regression | `node ui/scripts/w10/ui_regression_sweep.mjs` | `/tmp/w10-ui-regression.md` |
| test-suite | `bash scripts/w10/test_suite_runner.sh` | `/tmp/w10-test-suite.summary` |
| stress-audit | `node ui/scripts/w10/concurrent_stress_audit.mjs --mock 5 --live 3` | `/tmp/w10-stress-audit.md` |

The four sub-agents can be fanned out fully in parallel — none of them
mutate the others' state (the verifier is read-only; the test runner only
writes to `/tmp/`; stress-audit creates *new* events but reads only the
ones it itself triggered).
