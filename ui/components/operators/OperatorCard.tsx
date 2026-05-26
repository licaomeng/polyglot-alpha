import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Cpu, Wallet, Award, TrendingUp } from "lucide-react";

/**
 * Single operator card for the /operators marketplace listing.
 *
 * Displays the agent's display name + underlying model, wallet address,
 * reputation score, # bids won, and total builder-fee earnings. The `kind`
 * prop distinguishes the 4 in-house reference seeders from external
 * marketplace participants (currently 0 of them).
 */
export interface OperatorCardData {
  name: string;
  model: string;
  address: string;
  reputation: number;
  wins: number;
  totalFees: number;
  kind: "reference" | "external";
}

function shortAddress(address: string): string {
  if (address.length <= 12) return address;
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

export function OperatorCard({ operator }: { operator: OperatorCardData }) {
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
            {shortAddress(operator.address)}
          </code>
        </div>

        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-md border border-border/40 bg-background/40 p-2">
            <p className="flex items-center gap-1 text-[9px] uppercase tracking-wider text-muted-foreground">
              <Award className="h-2.5 w-2.5" aria-hidden /> Rep
            </p>
            <p className="font-mono text-sm font-semibold text-foreground">
              {operator.reputation.toFixed(2)}
            </p>
          </div>
          <div className="rounded-md border border-border/40 bg-background/40 p-2">
            <p className="text-[9px] uppercase tracking-wider text-muted-foreground">
              Wins
            </p>
            <p className="font-mono text-sm font-semibold text-foreground">
              {operator.wins}
            </p>
          </div>
          <div className="rounded-md border border-border/40 bg-background/40 p-2">
            <p className="flex items-center gap-1 text-[9px] uppercase tracking-wider text-muted-foreground">
              <TrendingUp className="h-2.5 w-2.5" aria-hidden /> Fees
            </p>
            <p className="font-mono text-sm font-semibold text-emerald-300">
              ${operator.totalFees.toFixed(2)}
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
