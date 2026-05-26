"use client";

import { useEvent } from "@/hooks/useEvent";
import { useEventStream } from "@/hooks/useEventStream";
import { EventTimeline } from "@/components/event/EventTimeline";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/EmptyState";
import { RealVsMockBadge } from "@/components/shared/RealVsMockBadge";
import { EventStatusBadge } from "@/components/event/EventStatusBadge";
import { WorkflowOverview } from "@/components/workflow/WorkflowOverview";
import { useEffect, useMemo } from "react";
import { useParams } from "next/navigation";
import { relativeTime } from "@/lib/utils";
import { usePhaseState } from "@/hooks/usePhaseState";
import { SSE_TO_PHASE_INDEX, type AnySseEventType } from "@/lib/api";
import { AgentDebatePanel } from "@/components/event/AgentDebatePanel";

export default function EventDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const { data: event, isLoading, isError, error } = useEvent(id);
  const { phases: livePhases, connected, latest } = useEventStream(id);
  const { setActivePhase } = usePhaseState();

  // When SSE delivers a phase transition, spotlight the matching DAG node +
  // timeline card so both views stay in lockstep with the backend.
  useEffect(() => {
    if (!latest) return;
    if (latest.type === "hello" || latest.type === "heartbeat") return;
    const idx = SSE_TO_PHASE_INDEX[latest.type as AnySseEventType];
    if (idx !== undefined) setActivePhase(idx);
  }, [latest, setActivePhase]);

  const merged = useMemo(() => {
    if (!event) return event;
    if (!livePhases?.length) return event;
    // Prefer the SSE-derived `status` per phase (more fresh) but keep
    // server-side `details` when SSE hasn't yet enriched them.
    const byName = new Map(livePhases.map((p) => [p.name, p]));
    const merged = event.phases.map((p) => {
      const live = byName.get(p.name);
      if (!live) return p;
      return {
        ...p,
        status: live.status,
        startedAt: live.startedAt ?? p.startedAt,
        completedAt: live.completedAt ?? p.completedAt,
        details: { ...(p.details ?? {}), ...(live.details ?? {}) },
      };
    });
    return { ...event, phases: merged };
  }, [event, livePhases]);

  if (isLoading) {
    return (
      <div className="container space-y-4 py-8">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-72" />
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (isError || !merged) {
    // The api helper throws `Error("API 404: …")` on non-2xx responses, so we
    // sniff the code to distinguish "event genuinely missing" (404) from a
    // transient network/timeout error and tailor the recovery hint.
    const errMsg = error instanceof Error ? error.message : "";
    const is404 = errMsg.includes("404");
    return (
      <div className="container space-y-4 py-8">
        <EmptyState
          title={is404 ? "Event not found" : isError ? "Couldn't load this event" : "Event not found"}
          description={
            is404
              ? `No event with id "${id}" exists in the backend. It may have been deleted or the link is malformed.`
              : isError
                ? `Backend at http://localhost:8000 didn't respond for id "${id}". It may be restarting or busy running the pipeline for another event.`
                : `No event matches id "${id}". The backend may be unreachable or the event has not been persisted yet.`
          }
        />
        <div className="flex justify-center gap-2">
          <a
            href="/events"
            className="text-xs text-primary underline-offset-2 hover:underline"
          >
            ← Back to events list
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="container space-y-6 py-8">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <RealVsMockBadge mode={merged.mode} />
          <EventStatusBadge status={merged.status} />
          {merged.marketSymbol && (
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {merged.marketSymbol}
            </span>
          )}
          <span className="text-[10px] text-muted-foreground">
            ingested {relativeTime(merged.ingestedAt)}
          </span>
          <span
            className={
              connected
                ? "ml-auto font-mono text-[10px] text-emerald-400"
                : "ml-auto font-mono text-[10px] text-muted-foreground"
            }
          >
            sse {connected ? "connected" : "offline"}
          </span>
        </div>
        <h1 className="text-2xl font-semibold leading-tight text-balance">{merged.headline}</h1>
        <p className="text-xs text-muted-foreground">{merged.source}</p>
      </header>

      <WorkflowOverview phases={merged.phases} />

      <section className="space-y-3">
        <h2 className="text-base font-semibold">Phase timeline</h2>
        <EventTimeline event={merged} />
      </section>

      <section className="space-y-3">
        <AgentDebatePanel event={merged} />
      </section>
    </div>
  );
}
