"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import { fetchAgent, type AgentProfile as AgentProfileType } from "@/lib/api";
import { AgentProfile } from "@/components/reputation/AgentProfile";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/EmptyState";
import { useEventList } from "@/hooks/useEventList";
import { EventStatusBadge } from "@/components/event/EventStatusBadge";
import { relativeTime } from "@/lib/utils";

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
  // The /events feed already polls every 5s — reuse it for the recent-runs
  // strip rather than firing a second request. Filtering happens client-side
  // because the v2 backend doesn't yet support `agent=` query filtering.
  const { data: events } = useEventList();
  const recent = (events ?? []).slice(0, 8);

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
    <div className="container space-y-4 py-8">
      <div className="grid gap-4 md:grid-cols-3">
        <div className="md:col-span-1">
          <AgentProfile agent={data} />
        </div>
        <Card className="md:col-span-2">
          <CardContent className="space-y-3 p-5">
            <div>
              <h2 className="text-sm font-semibold">Reputation + revenue</h2>
              <p className="text-xs text-muted-foreground">
                Cyan: reputation score (0–1). Magenta: cumulative builder-fee
                revenue (USD).
              </p>
            </div>
            <ReputationHistory data={data.history} />
          </CardContent>
        </Card>
      </div>

      {recent.length > 0 && (
        <Card>
          <CardContent className="space-y-3 p-5">
            <div className="flex items-baseline justify-between">
              <h2 className="text-sm font-semibold">Recent events</h2>
              <Link
                href="/events"
                className="text-[11px] text-muted-foreground hover:text-primary"
              >
                See all →
              </Link>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Latest pipeline runs (cross-agent — backend doesn&apos;t yet emit
              per-agent ownership in the summary feed).
            </p>
            <ul className="space-y-1.5">
              {recent.map((e) => (
                <li
                  key={e.id}
                  className="flex items-center justify-between gap-2 rounded border border-border/40 bg-secondary/20 px-3 py-2 text-xs"
                >
                  <Link
                    href={`/events/${e.id}`}
                    className="truncate text-left hover:text-primary"
                    title={e.headline}
                  >
                    {e.headline}
                  </Link>
                  <div className="flex shrink-0 items-center gap-2">
                    <EventStatusBadge status={e.status} />
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {relativeTime(e.ingestedAt)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
