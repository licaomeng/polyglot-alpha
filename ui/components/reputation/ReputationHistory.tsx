"use client";

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from "recharts";
import { formatUsd } from "@/lib/utils";

interface Point {
  ts: string;
  reputation: number;
  revenue: number;
}

export function ReputationHistory({ data }: { data: Point[] }) {
  if (!data?.length) return null;
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis
            dataKey="ts"
            tick={{ fontSize: 10 }}
            stroke="hsl(var(--muted-foreground))"
            tickFormatter={(v: string) =>
              new Date(v).toLocaleDateString([], { month: "short", day: "numeric" })
            }
            minTickGap={24}
          />
          <YAxis
            yAxisId="rep"
            domain={[0, 1]}
            tick={{ fontSize: 10 }}
            stroke="hsl(var(--primary))"
            width={36}
          />
          <YAxis
            yAxisId="rev"
            orientation="right"
            tick={{ fontSize: 10 }}
            stroke="hsl(var(--accent))"
            tickFormatter={(v: number) =>
              v >= 1000 ? `$${(v / 1000).toFixed(1)}k` : `$${v.toFixed(0)}`
            }
            width={48}
          />
          <Tooltip
            contentStyle={{
              background: "hsl(var(--card))",
              border: "1px solid hsl(var(--border))",
              fontSize: 11,
            }}
            labelFormatter={(l: string) => new Date(l).toLocaleString()}
            formatter={(v: number, name: string) =>
              name === "revenue"
                ? [formatUsd(v), "Revenue"]
                : [v.toFixed(2), "Reputation"]
            }
          />
          <Legend
            wrapperStyle={{ fontSize: 10 }}
            iconType="line"
            formatter={(value: string) =>
              value === "reputation" ? "Reputation (0–1)" : "Revenue ($)"
            }
          />
          <Line
            yAxisId="rep"
            type="monotone"
            dataKey="reputation"
            stroke="hsl(var(--primary))"
            strokeWidth={2}
            dot={false}
          />
          <Line
            yAxisId="rev"
            type="monotone"
            dataKey="revenue"
            stroke="hsl(var(--accent))"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
