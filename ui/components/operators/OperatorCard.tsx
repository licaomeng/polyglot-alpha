import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Cpu, Wallet, Award, TrendingUp } from "lucide-react";
import { shortAddr, formatReputation, formatUsd } from "@/lib/utils";
import { ClaimFeesButton } from "./ClaimFeesButton";
import { WithdrawStakeButton } from "./WithdrawStakeButton";

/**
 * Single operator card for the /operators marketplace listing.
 *
 * Displays the agent's display name + underlying model, wallet address,
 * reputation score, # bids won, and total builder-fee earnings. The `kind`
 * prop distinguishes the 3 in-house reference seeders from external
 * marketplace participants (currently 0 of them).
 *
 * `reputation`, `wins`, and `totalFees` are sourced live from the backend
 * `/leaderboard` endpoint (joined by wallet address). When the live value
 * is not yet available — backend still warming up, or the agent has not
 * appeared on the leaderboard yet — the field renders as "—" rather than a
 * fabricated number. See `ui/app/operators/page.tsx` for the wiring.
 */
export interface OperatorCardData {
  name: string;
  model: string;
  address: string;
  reputation?: number;
  wins?: number;
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

        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-md border border-border/40 bg-background/40 p-2">
            <p className="flex items-center gap-1 text-[9px] uppercase tracking-wider text-muted-foreground">
              <Award className="h-2.5 w-2.5" aria-hidden /> Rep
            </p>
            <p className="font-mono text-sm font-semibold text-foreground">
              {typeof operator.reputation === "number"
                ? formatReputation(operator.reputation)
                : UNKNOWN_PLACEHOLDER}
            </p>
          </div>
          <div className="rounded-md border border-border/40 bg-background/40 p-2">
            <p className="text-[9px] uppercase tracking-wider text-muted-foreground">
              Wins
            </p>
            <p className="font-mono text-sm font-semibold text-foreground">
              {typeof operator.wins === "number"
                ? operator.wins
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
