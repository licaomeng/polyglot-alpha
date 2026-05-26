"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  claimOperatorFees,
  fetchOperatorPendingFees,
  type ClaimFeesResponse,
  type PendingFeesResponse,
} from "@/lib/api";
import { arcscanTxUrl } from "@/lib/utils";
import { Coins, ExternalLink, Loader2, CheckCircle2, XCircle } from "lucide-react";

/**
 * Operator-facing button to withdraw accumulated 90% builder fees from
 * ``BuilderFeeRouter.claimFees``. Renders three visual states:
 *
 *   1. Loading pending balance — disabled "Claim Fees" with spinner.
 *   2. Pending balance == 0 — disabled with tooltip "No fees accumulated yet".
 *   3. Pending balance > 0 — enabled "Claim Fees ($X.XX)"; on click POSTs
 *      to ``/api/operators/{addr}/claim-fees`` and shows the resulting tx
 *      with an arcscan link (when the hash is real, not synthetic).
 *
 * Defaults to ``mode="mock"`` so the demo path never burns real gas. The
 * containing /operators page toggles ``mode`` based on the user's mode
 * selection elsewhere in the UI (TBD; for now mock is the safe default).
 */

interface ClaimFeesButtonProps {
  /** Operator wallet address (0x-prefixed, 42 chars). */
  address: string;
  /** Demo mode — "mock" returns 0xsim_ hash, "live" attempts real RPC. */
  mode?: "mock" | "live";
  /**
   * Optional initial pending value (e.g. from a parent leaderboard fetch).
   * The component still fetches its own pending balance to stay accurate
   * after a claim, but having an initial value avoids a flash of "Loading…".
   */
  initialPendingUsdc?: number;
  /** Optional callback invoked after a successful claim (parent can refresh). */
  onClaimed?: (result: ClaimFeesResponse) => void;
  /** Compact size for inline table/card use. */
  size?: "sm" | "default";
}

export function ClaimFeesButton({
  address,
  mode = "mock",
  initialPendingUsdc,
  onClaimed,
  size = "sm",
}: ClaimFeesButtonProps) {
  const [pending, setPending] = useState<number | null>(
    typeof initialPendingUsdc === "number" ? initialPendingUsdc : null,
  );
  const [isLoadingPending, setIsLoadingPending] = useState<boolean>(
    typeof initialPendingUsdc !== "number",
  );
  const [pendingError, setPendingError] = useState<string | null>(null);
  const [isClaiming, setIsClaiming] = useState(false);
  const [lastResult, setLastResult] = useState<ClaimFeesResponse | null>(null);
  const [claimError, setClaimError] = useState<string | null>(null);

  const refreshPending = useCallback(async () => {
    setIsLoadingPending(true);
    setPendingError(null);
    try {
      const res: PendingFeesResponse = await fetchOperatorPendingFees(address);
      setPending(res.pending_usdc);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      setPendingError(message);
    } finally {
      setIsLoadingPending(false);
    }
  }, [address]);

  useEffect(() => {
    void refreshPending();
  }, [refreshPending]);

  const handleClaim = useCallback(async () => {
    setIsClaiming(true);
    setClaimError(null);
    setLastResult(null);
    try {
      const result = await claimOperatorFees(address, mode);
      setLastResult(result);
      if (result.success) {
        // Refresh the pending balance — it should drop to 0 after a settle.
        await refreshPending();
        onClaimed?.(result);
      } else {
        setClaimError(
          mode === "live"
            ? "Chain call failed — local balance preserved for retry."
            : "Nothing to claim.",
        );
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      setClaimError(message);
    } finally {
      setIsClaiming(false);
    }
  }, [address, mode, onClaimed, refreshPending]);

  const pendingLabel = useMemo(() => {
    if (isLoadingPending) return "Loading…";
    if (pending === null) return "—";
    return pending.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 4,
    });
  }, [isLoadingPending, pending]);

  const isDisabled =
    isLoadingPending ||
    isClaiming ||
    pending === null ||
    pending <= 0 ||
    pendingError !== null;

  const tooltipText =
    pendingError !== null
      ? `Failed to load pending fees: ${pendingError}`
      : isLoadingPending
        ? "Loading pending balance…"
        : pending !== null && pending <= 0
          ? "No fees accumulated yet"
          : undefined;

  const txUrl = lastResult?.tx_hash ? arcscanTxUrl(lastResult.tx_hash) : null;

  return (
    <div className="space-y-2">
      <Button
        type="button"
        size={size}
        variant="outline"
        onClick={handleClaim}
        disabled={isDisabled}
        title={tooltipText}
        aria-label={`Claim ${pendingLabel} in builder fees for ${address}`}
        data-testid="claim-fees-button"
        data-pending-usdc={pending ?? ""}
      >
        {isClaiming ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            <span>Claiming…</span>
          </>
        ) : (
          <>
            <Coins className="h-3.5 w-3.5" aria-hidden />
            <span>Claim Fees ({pendingLabel})</span>
          </>
        )}
      </Button>

      {lastResult && lastResult.success ? (
        <div
          role="status"
          data-testid="claim-fees-success"
          className="flex flex-wrap items-center gap-1.5 rounded-md border border-emerald-500/40 bg-emerald-500/[0.05] px-2 py-1.5 text-[11px]"
        >
          <CheckCircle2
            className="h-3 w-3 text-emerald-400"
            aria-hidden
          />
          <span className="text-emerald-200">
            Claimed{" "}
            <strong className="font-mono">
              ${lastResult.amount_claimed_usdc.toFixed(4)}
            </strong>
            {lastResult.is_simulated ? " (simulated)" : ""}
          </span>
          {txUrl ? (
            <a
              href={txUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto inline-flex items-center gap-0.5 font-mono text-[10px] text-emerald-300 hover:underline"
              aria-label="View claim transaction on Arcscan"
            >
              {lastResult.tx_hash?.slice(0, 10)}…
              <ExternalLink className="h-2.5 w-2.5" aria-hidden />
            </a>
          ) : lastResult.tx_hash ? (
            <span
              className="ml-auto font-mono text-[10px] text-emerald-300/70"
              title="Synthetic mock-mode tx hash"
            >
              {lastResult.tx_hash}
            </span>
          ) : null}
        </div>
      ) : null}

      {claimError ? (
        <div
          role="alert"
          data-testid="claim-fees-error"
          className="flex items-start gap-1.5 rounded-md border border-destructive/40 bg-destructive/[0.05] px-2 py-1.5 text-[11px] text-destructive-foreground"
        >
          <XCircle
            className="mt-0.5 h-3 w-3 flex-shrink-0 text-destructive"
            aria-hidden
          />
          <span>{claimError}</span>
        </div>
      ) : null}
    </div>
  );
}
