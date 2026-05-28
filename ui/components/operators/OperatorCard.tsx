import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Cpu, Wallet, Trophy, TrendingUp } from "lucide-react";
import {
  shortAddr,
  formatReputation,
  formatUsd,
  formatWinsBids,
} from "@/lib/utils";
import { WinsBidsInfo } from "@/components/reputation/WinsBidsInfo";
import { ClaimFeesButton } from "./ClaimFeesButton";
import { WithdrawStakeButton } from "./WithdrawStakeButton";

/**
 * Single operator card for the /operators marketplace listing.
 *
 * Displays the agent's display name + underlying model, wallet address,
 * wins-over-bids ratio (primary signal, W14-D), total builder-fee earnings,
 * and the raw on-chain EMA reputation under an "advanced" detail row. The
 * `kind` prop distinguishes the 3 in-house reference seeders from external
 * marketplace participants (currently 0 of them).
 *
 * `reputation`, `wins`, `totalBids`, and `totalFees` are sourced live from
 * the backend `/leaderboard` endpoint (joined by wallet address). When the
 * live value is not yet available — backend still warming up, or the agent
 * has not appeared on the leaderboard yet — the field renders as "—"
 * rather than a fabricated number. See `ui/app/operators/page.tsx` for
 * the wiring.
 *
 * The on-chain EMA reputation is intentionally relegated to a secondary
 * row: the `ReputationRegistry.sol` `_fillSignal` has a known unit-scale
 * bug (W14-C) that pins the EMA at its 0.5 floor for realistic fees, so
 * leading with the wins/bids count gives operators an unambiguous metric
 * until the contract upgrade lands.
 */
export interface OperatorCardData {
  name: string;
  model: string;
  address: string;
  reputation?: number;
  wins?: number;
  totalBids?: number;
  totalFees?: number;
  kind: "reference" | "external";
}

const UNKNOWN_PLACEHOLDER = "—";

export function OperatorCard({
  operator,
  showClaimFees = false,
  claimMode = "mock",
}: {
  operator: OperatorCardData;
  /** When true, render an inline "Claim Fees" button for this operator. */
  showClaimFees?: boolean;
  /** Mock mode is the default — see ClaimFeesButton for semantics. */
  claimMode?: "mock" | "live";
}) {
  const isReference = operator.kind === "reference";
  return (
    <Card
      data-testid="operator-card"
      data-operator-kind={operator.kind}
      data-operator-name={operator.name}
      className={
        isReference
          ? "border-primary/30 bg-primary/[0.03]"
          : "border-emerald-500/30 bg-emerald-500/[0.03]"
      }
    >
      <CardContent className="space-y-3 p-5">
        <div className="flex items-start justify-between gap-2">
          <div className="space-y-1">
            <div className="flex items-center gap-1.5">
              <Cpu className="h-3.5 w-3.5 text-primary" aria-hidden />
              <h3 className="text-sm font-semibold leading-tight">
                {operator.name}
              </h3>
            </div>
            <p className="font-mono text-[10px] text-muted-foreground">
              {operator.model}
            </p>
          </div>
          <Badge variant={isReference ? "info" : "success"}>
            {isReference ? "Reference Seeder" : "External Operator"}
          </Badge>
        </div>

        <div className="flex items-center gap-1.5 rounded-md border border-border/40 bg-muted/20 px-2.5 py-1.5">
          <Wallet className="h-3 w-3 text-muted-foreground" aria-hidden />
          <code
            className="font-mono text-[10px] text-foreground/85"
            title={operator.address}
          >
            {shortAddr(operator.address)}
          </code>
          <button
            type="button"
            onClick={() => {
              navigator.clipboard?.writeText(operator.address).catch(() => {});
            }}
            aria-label={`Copy address ${operator.address}`}
            className="ml-auto rounded px-1 text-[10px] text-muted-foreground transition-colors hover:bg-accent/10 hover:text-foreground"
          >
            copy
          </button>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-md border border-border/40 bg-background/40 p-2">
            <p className="flex items-center gap-1 text-[9px] uppercase tracking-wider text-muted-foreground">
              <Trophy className="h-2.5 w-2.5" aria-hidden /> Wins / Bids
              <WinsBidsInfo
                className="ml-auto"
                ariaLabel={`Why wins / bids for ${operator.name}?`}
              />
            </p>
            <p
              className="font-mono text-sm font-semibold text-foreground"
              data-testid="operator-wins-bids"
              title={
                typeof operator.wins === "number" &&
                typeof operator.totalBids === "number"
                  ? `${operator.wins} wins out of ${operator.totalBids} bids entered`
                  : undefined
              }
            >
              {typeof operator.wins === "number" &&
              typeof operator.totalBids === "number"
                ? formatWinsBids(operator.wins, operator.totalBids)
                : UNKNOWN_PLACEHOLDER}
            </p>
          </div>
          <div className="rounded-md border border-border/40 bg-background/40 p-2">
            <p className="flex items-center gap-1 text-[9px] uppercase tracking-wider text-muted-foreground">
              <TrendingUp className="h-2.5 w-2.5" aria-hidden /> Fees
            </p>
            <p className="font-mono text-sm font-semibold text-emerald-300">
              {typeof operator.totalFees === "number"
                ? formatUsd(operator.totalFees)
                : UNKNOWN_PLACEHOLDER}
            </p>
          </div>
        </div>

        <div
          className="flex items-center justify-between gap-2 rounded-md border border-dashed border-border/30 bg-muted/[0.04] px-2 py-1"
          title="On-chain EMA reputation — calibrating in next contract upgrade (see ReputationRegistry.sol)"
        >
          <p className="text-[9px] uppercase tracking-wider text-muted-foreground/80">
            On-chain EMA <span className="text-muted-foreground/60">(adv.)</span>
          </p>
          <p className="font-mono text-[11px] text-muted-foreground">
            {typeof operator.reputation === "number"
              ? formatReputation(operator.reputation, { rawDecimal: true })
              : UNKNOWN_PLACEHOLDER}
          </p>
        </div>

        {showClaimFees ? (
          <div className="space-y-3 border-t border-border/40 pt-3">
            <ClaimFeesButton
              address={operator.address}
              mode={claimMode}
              initialPendingUsdc={
                typeof operator.totalFees === "number"
                  ? operator.totalFees
                  : undefined
              }
            />
            <WithdrawStakeButton
              address={operator.address}
              mode={claimMode}
            />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
