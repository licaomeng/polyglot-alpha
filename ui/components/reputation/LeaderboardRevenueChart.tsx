"use client";

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import type { LeaderboardEntry } from "@/lib/api";
import { formatUsd } from "@/lib/utils";

// Extracted into its own module so it can be dynamically imported
// (recharts ships >100 KB and we don't need it server-rendered).
export function LeaderboardRevenueChart({
  data,
}: {
  data: LeaderboardEntry[];
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
        <XAxis
          dataKey="alias"
          tick={{ fontSize: 10 }}
          stroke="hsl(var(--muted-foreground))"
        />
        <YAxis
          tick={{ fontSize: 10 }}
          stroke="hsl(var(--muted-foreground))"
          tickFormatter={(v: number) =>
            v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v.toString()
          }
        />
        <Tooltip
          contentStyle={{
            background: "hsl(var(--card))",
            border: "1px solid hsl(var(--border))",
            fontSize: 11,
          }}
          formatter={(v: number) => formatUsd(v)}
        />
        <Bar dataKey="revenueUsd" fill="hsl(var(--primary))" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
