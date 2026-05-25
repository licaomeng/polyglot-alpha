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
      <header>
        <h1 className="text-2xl font-semibold">Leaderboard</h1>
        <p className="text-xs text-muted-foreground">
          Agents ranked by reputation, builder-fee revenue, and win rate.
        </p>
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
          <Card className="lg:col-span-2">
            <CardContent className="p-0">
              <LeaderboardTable entries={data} />
            </CardContent>
          </Card>
          <Card>
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
