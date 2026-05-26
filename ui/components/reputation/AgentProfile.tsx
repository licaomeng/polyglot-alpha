import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { AgentProfile as AgentProfileType } from "@/lib/api";
import { ContractAddressDisplay } from "@/components/onchain/ContractAddressDisplay";
import { formatUsd } from "@/lib/utils";

/**
 * Per-persona metadata shown as a badge strip next to the alias. The three
 * canonical reference seeders in the demo all route through Claude Haiku 4.5
 * on the wire — they differ via system-prompt and temperature profile only
 * (see polyglot_alpha/llm.py). Persona names are kept so evaluators can map
 * each wallet to its behavioural fingerprint.
 */
const AGENT_META: Record<
  string,
  { provider: string; specialty: string; strategy: string }
> = {
  qwen: {
    provider: "Claude Haiku 4.5 · Qwen persona",
    specialty: "Mandarin → English macro",
    strategy: "Aggressive low-bid, fast settle",
  },
  gemini: {
    provider: "Claude Haiku 4.5 · Gemini persona",
    specialty: "General-purpose, fast",
    strategy: "Mid-band bid, high COMET",
  },
  deepseek: {
    provider: "Claude Haiku 4.5 · DeepSeek persona",
    specialty: "Reasoning-heavy translations",
    strategy: "Aggressive low-bid, slow settle",
  },
};

function lookupMeta(address: string): (typeof AGENT_META)[string] | undefined {
  const lower = address.toLowerCase();
  for (const key of Object.keys(AGENT_META)) {
    if (lower.includes(key)) return AGENT_META[key];
  }
  return undefined;
}

export function AgentProfile({ agent }: { agent: AgentProfileType }) {
  const total = agent.wins + agent.losses;
  const winRate = total > 0 ? (agent.wins / total) * 100 : 0;
  const meta = lookupMeta(agent.address);
  return (
    <Card>
      <CardContent className="space-y-4 p-5">
        <div>
          <h2 className="text-lg font-semibold">{agent.alias ?? "Agent"}</h2>
          <p className="text-xs text-muted-foreground">
            Cross-language alpha producer
          </p>
        </div>
        {meta && (
          <div className="flex flex-wrap items-center gap-1.5" aria-label="Agent metadata">
            <Badge variant="info" title="Underlying LLM provider">
              {meta.provider}
            </Badge>
            <Badge variant="secondary" title="Specialty / target domain">
              {meta.specialty}
            </Badge>
          </div>
        )}
        <ContractAddressDisplay label="addr" address={agent.address} />
        {meta && (
          <div className="rounded-md border border-border/60 bg-secondary/30 p-2 text-[11px] leading-relaxed text-muted-foreground">
            <span className="font-medium text-foreground">Bid strategy</span>:{" "}
            {meta.strategy}
          </div>
        )}
        <div className="grid grid-cols-2 gap-2 text-center lg:grid-cols-3">
          <Stat
            label="Reputation"
            value={agent.reputation.toFixed(2)}
            accent="primary"
            title="Reputation in [0, 1] · EWMA over recent fills, closed-IP weighting"
          />
          <Stat
            label="Revenue"
            value={formatUsd(agent.totalRevenue)}
            title="Cumulative builder-fee receipts (USDC, 0.4% per fill)"
          />
          <Stat
            label="Win rate"
            value={`${winRate.toFixed(0)}%`}
            title={`Auctions won (${agent.wins}) ÷ entered (${total})`}
          />
        </div>
        <p className="text-[10px] leading-relaxed text-muted-foreground">
          Reputation decays at EWMA α=0.85 — a single rejected submission still
          drops the score noticeably, so agents have skin in the game.{" "}
          <span className="text-muted-foreground/60">
            Slashing on chain: 0 events (testnet)
          </span>
        </p>
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  accent,
  title,
}: {
  label: string;
  value: string;
  accent?: "primary";
  title?: string;
}) {
  return (
    <div
      className="rounded-md border border-border/60 bg-secondary/30 p-2"
      title={title}
    >
      <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div
        className={
          accent === "primary"
            ? "mt-1 font-mono text-base text-primary"
            : "mt-1 font-mono text-base"
        }
      >
        {value}
      </div>
    </div>
  );
}
