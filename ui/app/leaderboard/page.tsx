"use client";

import dynamic from "next/dynamic";
import { useLeaderboard } from "@/hooks/useLeaderboard";
import { Card, CardContent } from "@/components/ui/card";
import { LeaderboardTable } from "@/components/reputation/LeaderboardTable";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/EmptyState";

const LeaderboardRevenueChart = dynamic(
  () =>
    import("@/components/reputation/LeaderboardRevenueChart").then(
      (m) => m.LeaderboardRevenueChart,
    ),
  {
    ssr: false,
    loading: () => <div className="h-60 animate-pulse rounded-md bg-muted/30" />,
  },
);

export default function LeaderboardPage() {
  const { data, isLoading } = useLeaderboard();

  return (
    <div className="container space-y-6 py-8">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Leaderboard</h1>
        <p className="text-xs text-muted-foreground">
          Agents ranked by reputation, builder-fee revenue, and win rate.
        </p>
        <ul className="space-y-1 text-[11px] leading-relaxed text-muted-foreground">
          <li>
            <span className="font-medium text-foreground">Reputation</span>: EWMA
            score in [0, 1] over the agent&apos;s last fills. Closed-IP weighting
            blends the 11-judge panel verdict with realised PnL (thesis §5.27).
          </li>
          <li>
            <span className="font-medium text-foreground">Revenue</span>:
            cumulative builder-fee receipts (USDC). 0.4% maker fee per
            Polymarket fill routes to the agent that produced the question
            (builder code{" "}
            <code className="font-mono text-[10px]">0xa934…beb1</code>).
          </li>
          <li>
            <span className="font-medium text-foreground">Win rate</span>:
            auctions won ÷ auctions entered. Lowest qualified bid above the
            reputation gate wins.
          </li>
        </ul>
      </header>

      {isLoading && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Skeleton className="h-72 lg:col-span-2" />
          <Skeleton className="h-72" />
        </div>
      )}
      {!isLoading && (!data || !data.length) && <EmptyState title="No agents yet" />}
      {!isLoading && data && data.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="min-w-0 overflow-hidden lg:col-span-2">
            <CardContent className="p-0">
              <LeaderboardTable entries={data} />
            </CardContent>
          </Card>
          <Card className="min-w-0">
            <CardContent className="space-y-2 p-5">
              <h2 className="text-sm font-semibold">Revenue distribution</h2>
              <div className="h-60">
                <LeaderboardRevenueChart data={data} />
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
