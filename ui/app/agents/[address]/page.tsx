"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { fetchAgent, type AgentProfile as AgentProfileType } from "@/lib/api";
import { AgentProfile } from "@/components/reputation/AgentProfile";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/EmptyState";

const ReputationHistory = dynamic(
  () =>
    import("@/components/reputation/ReputationHistory").then(
      (m) => m.ReputationHistory,
    ),
  {
    ssr: false,
    loading: () => <div className="h-64 animate-pulse rounded-md bg-muted/30" />,
  },
);

export default function AgentPage() {
  const params = useParams<{ address: string }>();
  const address = params?.address ?? "";
  const { data, isLoading } = useQuery<AgentProfileType | undefined>({
    queryKey: ["agent", address],
    queryFn: () => fetchAgent(address),
    retry: 1,
  });

  if (isLoading) {
    return (
      <div className="container grid gap-4 py-8 md:grid-cols-3">
        <Skeleton className="h-64 md:col-span-1" />
        <Skeleton className="h-64 md:col-span-2" />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="container py-8">
        <EmptyState
          title="Agent not found"
          description={`No profile available for ${address}.`}
        />
      </div>
    );
  }

  return (
    <div className="container grid gap-4 py-8 md:grid-cols-3">
      <div className="md:col-span-1">
        <AgentProfile agent={data} />
      </div>
      <Card className="md:col-span-2">
        <CardContent className="space-y-3 p-5">
          <div>
            <h2 className="text-sm font-semibold">Reputation + revenue</h2>
            <p className="text-xs text-muted-foreground">
              Cyan: reputation score (0–1). Magenta: cumulative builder-fee revenue (USD).
            </p>
          </div>
          <ReputationHistory data={data.history} />
        </CardContent>
      </Card>
    </div>
  );
}
