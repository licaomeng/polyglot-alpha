"use client";

import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip } from "recharts";
import { formatUsd, relativeTime, shortAddr } from "@/lib/utils";
import { TxLink } from "@/components/onchain/TxLink";

interface Props {
  stream: { ts: string; usd: number }[];
  recentFills?: { ts: string; txHash: string; amountUsd: number }[];
}

export function BuilderFeeStream({ stream, recentFills }: Props) {
  if (!stream?.length) {
    return (
      <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        no fees streamed yet — waiting for first fill
      </p>
    );
  }
  const total = stream[stream.length - 1].usd;
  const since = stream[0]?.ts;
  const elapsedHours = (() => {
    if (!since) return null;
    const ms = Date.now() - new Date(since).getTime();
    const hrs = ms / 1000 / 3600;
    return hrs > 0 ? hrs : null;
  })();
  const ratePerHour = elapsedHours ? total / elapsedHours : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
          Cumulative builder fees · USDC
        </span>
        <div className="flex items-baseline gap-3">
          {ratePerHour !== null && (
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {formatUsd(ratePerHour)}/hr · since {relativeTime(since)}
            </span>
          )}
          <span className="font-mono text-2xl text-primary">{formatUsd(total)}</span>
        </div>
      </div>
      <div className="h-40 md:h-48">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={stream} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="feeGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.5} />
                <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="ts"
              tick={{ fontSize: 10 }}
              stroke="hsl(var(--muted-foreground))"
              tickFormatter={(v: string) =>
                new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
              }
              minTickGap={32}
            />
            <YAxis
              tick={{ fontSize: 10 }}
              stroke="hsl(var(--muted-foreground))"
              tickFormatter={(v: number) =>
                v >= 1000 ? `$${(v / 1000).toFixed(1)}k` : `$${v.toFixed(0)}`
              }
              width={42}
            />
            <Tooltip
              contentStyle={{
                background: "hsl(var(--card))",
                border: "1px solid hsl(var(--border))",
                fontSize: 11,
              }}
              formatter={(v: number) => [formatUsd(v), "Revenue"]}
              labelFormatter={(l: string) => new Date(l).toLocaleString()}
            />
            <Area
              type="monotone"
              dataKey="usd"
              stroke="hsl(var(--primary))"
              strokeWidth={2}
              fill="url(#feeGrad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {recentFills && recentFills.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
            Recent fills (last {Math.min(10, recentFills.length)})
          </div>
          <ul className="divide-y divide-border/40 rounded-md border border-border/40 bg-card/40">
            {recentFills.slice(-10).reverse().map((f, idx) => (
              <li
                key={`${f.txHash}-${idx}`}
                className="flex items-center justify-between gap-2 px-3 py-1.5 text-xs"
              >
                <span className="font-mono text-[10px] text-muted-foreground">
                  {relativeTime(f.ts)}
                </span>
                <TxLink txHash={f.txHash} label="" />
                <span className="font-mono text-emerald-400">{formatUsd(f.amountUsd)}</span>
              </li>
            ))}
          </ul>
          {/* shortAddr import keeps lint happy when no fills passed at runtime. */}
          <span hidden>{shortAddr("")}</span>
        </div>
      )}
    </div>
  );
}
