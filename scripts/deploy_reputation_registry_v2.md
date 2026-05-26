# Deploy ReputationRegistry v2 — Procedure

**Status:** code ready, **not yet deployed**. Awaiting user approval.

## What this deploys

A new `ReputationRegistry` contract with two corrections to the deployed v1
(`REPUTATION_REGISTRY_ADDRESS`):

| Fix | Symbol | Before | After |
|-----|--------|--------|-------|
| β   | `_fillSignal` fee scaling | `x = cumFees / 100` (treats USDC 6-dec as 1e18 fp — off by 1e12) | `x = (cumFees * 1e12) / 100` (rescale USDC → 1e18 first) |
| α   | initial score on first touch | `ONE = 1e18` (1.0) | `HALF = 5e17` (0.5) |

See `outputs/reputation_v2_fix.patch` for the diff.

## Pre-deploy checklist

1. **Compile artifacts are fresh.** `cd contracts && ~/.foundry/bin/forge build`
   must finish cleanly. The deploy script reads bytecode from
   `contracts/out/ReputationRegistry.sol/ReputationRegistry.json`.

2. **Tests pass.** `cd contracts && ~/.foundry/bin/forge test` must report all
   30 tests passing.

3. **Wallet funded.** Check `HACKATHON_WALLET_PRIVATE_KEY`'s Arc-testnet balance
   covers ~5 USDC-equivalent of gas (the constructor + 3 authorization calls
   together used ~1.5M gas on v1 deploy).

4. **Simulation reviewed.** Run `.venv/bin/python scripts/simulate_ema.py` and
   confirm v1-vs-v2 deltas match expectations (see README §11.4 for the
   reference table).

## Dry-run (recommended first)

```bash
# Source the env so the script picks up HACKATHON_WALLET_PRIVATE_KEY etc.
source .env

.venv/bin/python scripts/deploy_reputation_registry_v2.py
```

This prints what *would* happen without broadcasting any transaction. Expect
output like:

```
DRY-RUN — would do the following (re-run with --confirm to broadcast):
  1. deploy ReputationRegistry (constructor takes no args)
  2. setAuthorized(<deployer>, true)  # operator EOA
  3. setAuthorized(0xE046...07a, true)  # TranslationAuction
  3. setAuthorized(0xcE75...0e5, true)  # BuilderFeeRouter
  3. setAuthorized(0x1eE7...d9a, true)  # JudgePanel
```

## Actual deploy

```bash
source .env
.venv/bin/python scripts/deploy_reputation_registry_v2.py --confirm
```

The script will:

1. Deploy a new `ReputationRegistry` contract (operator is auto-authorized by
   the constructor).
2. Authorize each downstream contract address that was supplied via env vars
   (`TRANSLATION_AUCTION_ADDRESS`, `BUILDER_FEE_ROUTER_ADDRESS`,
   `JUDGE_PANEL_ADDRESS`) so they can push state.
3. Write a JSON artifact to `outputs/deployment_reputation_v2.json`.
4. Print the new address.

## Post-deploy

1. **Record the address** in `.env`:

   ```bash
   echo "REPUTATION_REGISTRY_V2_ADDRESS=0x..." >> .env
   ```

2. **Re-run the chain consistency verifier** against the new address to confirm
   it accepts updates:

   ```bash
   REPUTATION_REGISTRY_ADDRESS=$REPUTATION_REGISTRY_V2_ADDRESS \
       .venv/bin/python scripts/verify_chain_consistency.py
   ```

3. **Cutover.** Once the v2 contract has been smoke-tested in isolation, point
   `REPUTATION_REGISTRY_ADDRESS` at the new address and restart the
   orchestrator. The v1 contract remains on-chain as a historical record —
   leave the existing `REPUTATION_REGISTRY_ADDRESS_OLD_*` pattern in `.env` for
   provenance.

4. **No backfill required.** Reputation state is per-address EWMA; the v2
   contract starts every agent fresh at `HALF` on first touch. Historical
   `BuilderFeeRouter.cumulativeFees` is still readable on-chain as the audit
   trail of past activity.

## Idempotency

If `REPUTATION_REGISTRY_V2_ADDRESS` is already set in the environment and the
address has bytecode on-chain, the script exits early — re-running it is
safe and is a no-op.

## Rollback

If post-deploy verification fails, simply do **not** update
`REPUTATION_REGISTRY_ADDRESS`. The deployed v1 contract continues operating
unaffected; the v2 contract is dormant until something points at it.
