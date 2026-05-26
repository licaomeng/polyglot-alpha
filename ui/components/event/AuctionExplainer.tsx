"use client";

import { useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import { Trophy, Info } from "lucide-react";
import { shortAddr, formatUsd, formatReputation, cn } from "@/lib/utils";
import type { BidEntry, EventDetail } from "@/lib/api";

/**
 * The auction settles on:
 *
 *   score = bid_amount * 1e18 / max(reputation, 1.0)
 *
 * Lower score wins (cheaper bid scaled by reputation) — matching the
 * smart-contract math in `contracts/PolyglotAuction.sol`.
 *
 * NOTE: when all reputations are 0 we fall back to "lowest bid wins"
 * because max(reputation, 1.0) is constant — that's the typical case for
 * fresh test runs, and the explainer makes that explicit.
 */

interface ScoredBid extends BidEntry {
  score: number;
  candidateHash?: string;
  stakeAmount?: number;
}

interface AuctionExplainerProps {
  event: EventDetail;
}

function scoreBid(bid: BidEntry): number {
  const rep = Math.max(bid.reputation ?? 0, 1.0);
  // We render the math as `bid / max(rep, 1)` since multiplying by 1e18
  // is just unit scaling — the UI shows the more intuitive ratio.
  return bid.bid / rep;
}

export function AuctionExplainer({ event }: AuctionExplainerProps) {
  const bids = event.bids ?? [];

  const scored = useMemo<ScoredBid[]>(() => {
    return bids
      .map((b) => {
        const loose = b as BidEntry & {
          candidate_hash?: string;
          stake_amount?: number;
        };
        return {
          ...b,
          score: scoreBid(b),
          candidateHash: loose.candidate_hash,
          stakeAmount: loose.stake_amount,
        };
      })
      .sort((a, b) => a.score - b.score);
  }, [bids]);

  if (scored.length === 0) {
    return (
      <section
        aria-label="Auction explainer"
        data-testid="auction-explainer-empty"
        className="rounded-xl border border-dashed border-border/50 bg-muted/10 p-5"
      >
        <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          no bids recorded — auction has not settled yet
        </p>
      </section>
    );
  }

  const winner = scored.find((b) => b.winner) ?? scored[0];
  const allRepsZero = bids.every((b) => (b.reputation ?? 0) === 0);

  return (
    <section
      aria-label="Auction explainer"
      data-testid="auction-explainer"
      className="space-y-3"
    >
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold">USDC Auction · Bid math</h3>
          <Tooltip
            widthClassName="max-w-sm"
            content={
              <div className="space-y-1.5">
                <p className="font-mono text-[11px] font-semibold text-foreground">
                  Settlement formula
                </p>
                <p className="font-mono text-[10px] text-cyan-300/90">
                  score = bid_amount × 1e18 / max(reputation, 1.0)
                </p>
                <p className="text-foreground/85">
                  Lower score wins. The contract pins reputation at a minimum of
                  1.0 so a fresh agent (rep=0) still bids on equal footing — in
                  that case the lowest absolute bid wins.
                </p>
              </div>
            }
          >
            <button
              type="button"
              aria-label="Explain auction formula"
              className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-border/50 text-muted-foreground hover:border-primary/60 hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Info className="h-2.5 w-2.5" aria-hidden />
            </button>
          </Tooltip>
        </div>
        <p className="text-[11px] text-muted-foreground">
          {allRepsZero
            ? "All bidders had reputation 0 — equal footing. Lowest absolute bid wins."
            : "Bids scored by reputation-adjusted price. Lowest score wins."}
        </p>
      </header>

      <div className="space-y-2">
        {scored.map((bid, idx) => {
          const isWinner = bid.winner === true || bid === winner;
          return (
            <Card
              key={bid.agent ?? idx}
              className={cn(
                "border-border/60",
                isWinner &&
                  "border-amber-400/60 bg-amber-500/[0.04] ring-1 ring-amber-400/30",
              )}
            >
              <CardContent className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-[1fr_auto_auto_auto_auto]">
                <div className="flex items-center gap-2">
                  {isWinner && (
                    <Trophy
                      className="h-3.5 w-3.5 flex-shrink-0 text-amber-400"
                      aria-label="winner"
                    />
                  )}
                  <span className="font-mono text-xs text-foreground/90">
                    {shortAddr(bid.agent)}
                  </span>
                  {isWinner && (
                    <Badge
                      variant="warning"
                      className="font-mono text-[9px] uppercase tracking-wider"
                    >
                      winner
                    </Badge>
                  )}
                </div>
                <div className="text-right">
                  <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                    bid
                  </p>
                  <p className="font-mono text-xs text-foreground">
                    {formatUsd(bid.bid, 2)}
                  </p>
                </div>
                <div className="text-right">
                  <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                    rep.
                  </p>
                  <p
                    className="font-mono text-xs text-foreground"
                    title={`Raw decimal: ${bid.reputation.toFixed(4)}`}
                  >
                    {formatReputation(bid.reputation, { rawDecimal: true })}
                  </p>
                </div>
                <div className="text-right">
                  <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                    stake
                  </p>
                  <p className="font-mono text-xs text-foreground">
                    {bid.stakeAmount !== undefined
                      ? formatUsd(bid.stakeAmount, 2)
                      : "—"}
                  </p>
                </div>
                <div className="text-right">
                  <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                    score
                  </p>
                  <p
                    className={cn(
                      "font-mono text-xs",
                      isWinner ? "text-amber-300" : "text-foreground",
                    )}
                  >
                    {bid.score.toFixed(4)}
                  </p>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {winner && (
        <div className="rounded-md border border-amber-400/30 bg-amber-500/[0.04] p-3 text-xs">
          <p className="font-mono text-[10px] uppercase tracking-wider text-amber-300/80">
            Why this winner?
          </p>
          <p className="mt-1 text-foreground/90">
            <span className="font-mono text-amber-300">
              {shortAddr(winner.agent)}
            </span>{" "}
            won with the lowest reputation-adjusted score (
            <span className="font-mono">{formatUsd(winner.bid, 2)}</span> /{" "}
            <span className="font-mono">
              max({formatReputation(winner.reputation, { rawDecimal: true })}, 1.0)
            </span>{" "}
            = <span className="font-mono">{winner.score.toFixed(4)}</span>
            ).
          </p>
        </div>
      )}
    </section>
  );
}
