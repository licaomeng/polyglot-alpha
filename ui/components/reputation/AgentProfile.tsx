import { Card, CardContent } from "@/components/ui/card";
import type { AgentProfile as AgentProfileType } from "@/lib/api";
import { ContractAddressDisplay } from "@/components/onchain/ContractAddressDisplay";
import { formatUsd } from "@/lib/utils";

export function AgentProfile({ agent }: { agent: AgentProfileType }) {
  const total = agent.wins + agent.losses;
  const winRate = total > 0 ? (agent.wins / total) * 100 : 0;
  return (
    <Card>
      <CardContent className="space-y-4 p-5">
        <div>
          <h2 className="text-lg font-semibold">{agent.alias ?? "Agent"}</h2>
          <p className="text-xs text-muted-foreground">Cross-language alpha producer</p>
        </div>
        <ContractAddressDisplay label="addr" address={agent.address} />
        <div className="grid grid-cols-3 gap-2 text-center">
          <Stat label="Reputation" value={agent.reputation.toFixed(2)} accent="primary" />
          <Stat label="Revenue" value={formatUsd(agent.totalRevenue)} />
          <Stat label="Win rate" value={`${winRate.toFixed(0)}%`} />
        </div>
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: "primary";
}) {
  return (
    <div className="rounded-md border border-border/60 bg-secondary/30 p-2">
      <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div
        className={
          accent === "primary" ? "mt-1 font-mono text-base text-primary" : "mt-1 font-mono text-base"
        }
      >
        {value}
      </div>
    </div>
  );
}
