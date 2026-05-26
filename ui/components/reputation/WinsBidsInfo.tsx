"use client";

import { Info } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * Small (i) info-icon button with a tooltip explaining why operator/agent
 * cards lead with a wins/bids ratio rather than the on-chain EMA reputation
 * score (W14-D).
 *
 * The on-chain `ReputationRegistry.sol` has a known `_fillSignal`
 * unit-scale bug (W14-C investigation): the EMA stays pinned at the 0.5
 * floor for any realistic fee, so even consistently winning agents render
 * as 0.49–0.75. Until the contract upgrade ships, the UI leads with the
 * unambiguous off-chain count and links the raw chain value under
 * "advanced" detail rows.
 *
 * Designed to be inlined next to a column header or stat label. The
 * trigger is a properly-labelled focusable <button> so the tooltip is
 * reachable by keyboard *and* screen-readers (the underlying <Tooltip>
 * opens on both hover and focus).
 */
export function WinsBidsInfo({
  className,
  ariaLabel = "Why wins / bids?",
}: {
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <Tooltip
      side="bottom"
      align="end"
      widthClassName="max-w-sm"
      content={
        // NB: rendered with span+block (not <div>/<p>) so the markup is
        // valid HTML even when the trigger is embedded inside a <p>
        // (e.g. the operator-card stat label) — avoids hydration mismatch.
        // `normal-case` / `tracking-normal` resets the inherited
        // `uppercase tracking-wider` from <TH> when the tooltip lives in a
        // table-header context (leaderboard column trigger).
        <span className="block space-y-1.5 normal-case tracking-normal">
          <span className="block font-mono text-[11px] font-semibold text-foreground">
            Win rate (wins / bids entered)
          </span>
          <span className="block text-foreground/85">
            Shown as the primary signal. On-chain EMA reputation is also
            tracked but is calibrating in the next contract upgrade — see
            <span className="font-mono"> ReputationRegistry.sol</span>. The
            raw EMA is still available under the &ldquo;advanced&rdquo; row
            for operators that need the chain value.
          </span>
        </span>
      }
    >
      <button
        type="button"
        aria-label={ariaLabel}
        className={
          "inline-flex h-4 w-4 items-center justify-center rounded-full border border-border/50 text-muted-foreground hover:border-primary/60 hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring" +
          (className ? ` ${className}` : "")
        }
      >
        <Info className="h-2.5 w-2.5" aria-hidden />
      </button>
    </Tooltip>
  );
}
