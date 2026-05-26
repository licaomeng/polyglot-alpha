"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  fetchOperatorStakeStatus,
  withdrawOperatorStake,
  type StakeStatusResponse,
  type WithdrawStakeResponse,
} from "@/lib/api";
import { arcscanTxUrl } from "@/lib/utils";
import {
  ArrowDownToLine,
  ExternalLink,
  Loader2,
  CheckCircle2,
  XCircle,
} from "lucide-react";

/**
 * Operator-facing button to withdraw the 5 USDC auction-contract stake
 * (``TranslationAuction.withdrawStake()``). Mirrors ``ClaimFeesButton`` in
 * shape and state machine; the two sit side-by-side on the OperatorCard.
 *
 * Renders four visual states:
 *
 *   1. Loading stake status — disabled "Withdraw Stake" with spinner.
 *   2. ``staked=false`` — disabled with tooltip "No active stake".
 *   3. ``staked=true`` but ``can_withdraw=false`` (lock window) — disabled
 *      with tooltip "Stake locked until block N".
 *   4. ``can_withdraw=true`` — enabled "Withdraw Stake ($X.XX)"; on click
 *      POSTs to ``/api/operators/{addr}/withdraw-stake`` and shows the
 *      resulting tx with an arcscan link (real hash) or muted text
 *      (synthetic ``0xsim_…``).
 *
 * Defaults to ``mode="mock"`` so the demo path never burns real gas. The
 * containing /operators page toggles ``mode`` based on the user's mode
 * selection elsewhere in the UI; for now mock is the safe default.
 */

interface WithdrawStakeButtonProps {
  /** Operator wallet address (0x-prefixed, 42 chars). */
  address: string;
  /** Demo mode — "mock" returns a 0xsim_ hash, "live" attempts real RPC. */
  mode?: "mock" | "live";
  /** Optional callback invoked after a successful withdraw (parent refreshes). */
  onWithdrawn?: (result: WithdrawStakeResponse) => void;
  /** Compact size for inline card use. */
  size?: "sm" | "default";
}

export function WithdrawStakeButton({
  address,
  mode = "mock",
  onWithdrawn,
  size = "sm",
}: WithdrawStakeButtonProps) {
  const [stakeStatus, setStakeStatus] = useState<StakeStatusResponse | null>(
    null,
  );
  const [isLoadingStatus, setIsLoadingStatus] = useState<boolean>(true);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [isWithdrawing, setIsWithdrawing] = useState(false);
  const [lastResult, setLastResult] = useState<WithdrawStakeResponse | null>(
    null,
  );
  const [withdrawError, setWithdrawError] = useState<string | null>(null);

  const refreshStatus = useCallback(async () => {
    setIsLoadingStatus(true);
    setStatusError(null);
    try {
      const res = await fetchOperatorStakeStatus(address);
      setStakeStatus(res);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      setStatusError(message);
    } finally {
      setIsLoadingStatus(false);
    }
  }, [address]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  const handleWithdraw = useCallback(async () => {
    setIsWithdrawing(true);
    setWithdrawError(null);
    setLastResult(null);
    try {
      const result = await withdrawOperatorStake(address, mode);
      setLastResult(result);
      if (result.success) {
        await refreshStatus();
        onWithdrawn?.(result);
      } else {
        setWithdrawError("Withdraw reported no recovered stake.");
      }
    } catch (err) {
      const raw = err instanceof Error ? err.message : "Unknown error";
      // Decode the structured detail surfaced by the API helper so the
      // user sees a humane line rather than a JSON blob.
      let friendly = raw;
      if (/stake_locked/i.test(raw)) {
        const blockMatch = raw.match(/"locked_until_block":\s*(\d+)/);
        friendly = blockMatch
          ? `Stake is locked until block ${blockMatch[1]}.`
          : "Stake is locked under the 72h slashable window.";
      } else if (/no_stake_to_withdraw/i.test(raw)) {
        friendly = "No active stake to withdraw.";
      } else if (/API 503/i.test(raw)) {
        friendly = "Arc RPC unavailable — try again shortly.";
      }
      setWithdrawError(friendly);
    } finally {
      setIsWithdrawing(false);
    }
  }, [address, mode, onWithdrawn, refreshStatus]);

  const stakeLabel = useMemo(() => {
    if (isLoadingStatus) return "Loading…";
    if (!stakeStatus) return "—";
    return stakeStatus.amount_usdc.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 4,
    });
  }, [isLoadingStatus, stakeStatus]);

  const isDisabled =
    isLoadingStatus ||
    isWithdrawing ||
    statusError !== null ||
    !stakeStatus ||
    !stakeStatus.can_withdraw;

  const tooltipText = useMemo(() => {
    if (statusError !== null) {
      return `Failed to load stake status: ${statusError}`;
    }
    if (isLoadingStatus) return "Loading stake status…";
    if (!stakeStatus) return undefined;
    if (!stakeStatus.staked) return "No active stake";
    if (
      stakeStatus.staked &&
      !stakeStatus.can_withdraw &&
      stakeStatus.locked_until_block !== null
    ) {
      return `Stake locked until block ${stakeStatus.locked_until_block}`;
    }
    if (stakeStatus.staked && !stakeStatus.can_withdraw) {
      return "Stake not yet withdrawable";
    }
    return undefined;
  }, [statusError, isLoadingStatus, stakeStatus]);

  const txUrl = lastResult?.tx_hash ? arcscanTxUrl(lastResult.tx_hash) : null;

  return (
    <div className="space-y-2">
      <Button
        type="button"
        size={size}
        variant="outline"
        onClick={handleWithdraw}
        disabled={isDisabled}
        title={tooltipText}
        aria-label={`Withdraw ${stakeLabel} bidding stake for ${address}`}
        data-testid="withdraw-stake-button"
        data-stake-usdc={stakeStatus?.amount_usdc ?? ""}
        data-can-withdraw={stakeStatus?.can_withdraw ? "true" : "false"}
      >
        {isWithdrawing ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            <span>Withdrawing…</span>
          </>
        ) : (
          <>
            <ArrowDownToLine className="h-3.5 w-3.5" aria-hidden />
            <span>Withdraw Stake ({stakeLabel})</span>
          </>
        )}
      </Button>

      {lastResult && lastResult.success ? (
        <div
          role="status"
          data-testid="withdraw-stake-success"
          className="flex flex-wrap items-center gap-1.5 rounded-md border border-emerald-500/40 bg-emerald-500/[0.05] px-2 py-1.5 text-[11px]"
        >
          <CheckCircle2
            className="h-3 w-3 text-emerald-400"
            aria-hidden
          />
          <span className="text-emerald-200">
            Recovered{" "}
            <strong className="font-mono">
              ${lastResult.amount_recovered_usdc.toFixed(4)}
            </strong>
            {lastResult.is_simulated ? " (simulated)" : ""}
          </span>
          {txUrl ? (
            <a
              href={txUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto inline-flex items-center gap-0.5 font-mono text-[10px] text-emerald-300 hover:underline"
              aria-label="View withdraw transaction on Arcscan"
            >
              {lastResult.tx_hash?.slice(0, 10)}…
              <ExternalLink className="h-2.5 w-2.5" aria-hidden />
            </a>
          ) : lastResult.tx_hash ? (
            <span
              className="ml-auto font-mono text-[10px] text-emerald-300/70"
              title="Synthetic mock-mode tx hash"
              data-testid="withdraw-stake-sim-tx"
            >
              {lastResult.tx_hash}
            </span>
          ) : null}
        </div>
      ) : null}

      {withdrawError ? (
        <div
          role="alert"
          data-testid="withdraw-stake-error"
          className="flex items-start gap-1.5 rounded-md border border-destructive/40 bg-destructive/[0.05] px-2 py-1.5 text-[11px] text-destructive-foreground"
        >
          <XCircle
            className="mt-0.5 h-3 w-3 flex-shrink-0 text-destructive"
            aria-hidden
          />
          <span>{withdrawError}</span>
        </div>
      ) : null}
    </div>
  );
}
