import type { ReactNode } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { AgentProfile as AgentProfileType } from "@/lib/api";
import { ContractAddressDisplay } from "@/components/onchain/ContractAddressDisplay";
import { formatReputation, formatUsd, formatWinsBids } from "@/lib/utils";
import { WinsBidsInfo } from "@/components/reputation/WinsBidsInfo";

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
        <div className="grid grid-cols-2 gap-2 text-center">
          <Stat
            label={
              <span className="inline-flex items-center justify-center gap-1">
                Wins / Bids
                <WinsBidsInfo ariaLabel={`Why wins / bids for ${agent.alias ?? "agent"}?`} />
              </span>
            }
            value={formatWinsBids(agent.wins, total)}
            accent="primary"
            title={`${agent.wins} wins out of ${total} bids entered`}
          />
          <Stat
            label="Revenue"
            value={formatUsd(agent.totalRevenue)}
            title="Cumulative builder-fee receipts (USDC, 0.4% per fill)"
          />
        </div>
        <div
          className="flex items-center justify-between gap-2 rounded-md border border-dashed border-border/30 bg-muted/[0.04] px-2 py-1.5"
          title="On-chain EMA reputation — calibrating in next contract upgrade (see ReputationRegistry.sol)"
        >
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground/80">
            On-chain EMA <span className="text-muted-foreground/60">(advanced)</span>
          </p>
          <p className="font-mono text-[11px] text-muted-foreground">
            {formatReputation(agent.reputation, { rawDecimal: true })}
          </p>
        </div>
        <p className="text-[10px] leading-relaxed text-muted-foreground">
          Wins / bids is the primary signal. The on-chain EMA (α=0.85) is
          tracked alongside but is calibrating — the `_fillSignal` term in
          <span className="font-mono"> ReputationRegistry.sol</span> is being
          rescaled in the next contract upgrade, so the raw chain value is
          surfaced under &ldquo;advanced&rdquo; rather than the headline metric.{" "}
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
  label: ReactNode;
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
