"use client";

import { useState, useMemo } from "react";
import { EventCard } from "@/components/event/EventCard";
import { useEventList } from "@/hooks/useEventList";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/EmptyState";
import { Button } from "@/components/ui/button";
import { Search, AlertTriangle } from "lucide-react";
import {
  BUCKET_LABEL,
  BUCKET_TOOLTIP,
  STATUS_BUCKETS,
  bucketMatches,
  type StatusBucket,
} from "@/lib/status";

// All filter taxonomy lives in `lib/status.ts` — single source of truth so
// the events page, history page, and status badge can never drift. See
// `lib/status.ts` for the bucket → canonical-status mapping.

export default function EventsPage() {
  const { data, isLoading, isError, refetch, isFetching } = useEventList();
  const [filter, setFilter] = useState<StatusBucket>("all");
  const [query, setQuery] = useState("");

  const items = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data
      .filter((e) => bucketMatches(String(e.status), filter))
      .filter((e) => {
        if (q.length === 0) return true;
        // Backend can return null `headline` / empty `source` for legacy or
        // partial rows; coerce safely so the search never blows up.
        const headline = (e.headline ?? "").toLowerCase();
        const source = (e.source ?? "").toLowerCase();
        return headline.includes(q) || source.includes(q);
      });
  }, [data, filter, query]);

  return (
    <div className="container space-y-6 py-8">
      <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Events</h1>
          <p className="text-xs text-muted-foreground">
            Live + recent runs across the full 7-phase lifecycle (11-node workflow).
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:flex-wrap">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              className="h-10 w-full rounded-md border border-input bg-background pl-7 pr-3 text-base sm:h-9 sm:w-56 sm:text-xs focus:outline-none focus:ring-2 focus:ring-ring"
              placeholder="Search headlines…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search events"
            />
          </div>
          <div
            className="-mx-1 flex gap-1 overflow-x-auto px-1 py-0.5 sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0 sm:py-0"
            role="group"
            aria-label="Filter by status"
          >
            {STATUS_BUCKETS.map((b) => (
              <Button
                key={b}
                variant={b === filter ? "default" : "outline"}
                size="sm"
                onClick={() => setFilter(b)}
                aria-pressed={b === filter}
                title={BUCKET_TOOLTIP[b]}
                className="min-h-[40px] shrink-0 sm:min-h-0"
              >
                {BUCKET_LABEL[b]}
              </Button>
            ))}
          </div>
        </div>
      </header>

      {isError && !data && (
        <div
          role="alert"
          className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-4 text-xs"
        >
          <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" aria-hidden />
          <div className="space-y-1">
            <p className="font-medium text-destructive">
              Couldn&apos;t reach the backend at http://localhost:8000
            </p>
            <p className="text-muted-foreground">
              The FastAPI server may be restarting, queued behind a long-running
              `/trigger/event` call, or unreachable from the browser. Try again
              in a few seconds.
            </p>
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              onClick={() => refetch()}
              disabled={isFetching}
            >
              {isFetching ? "Retrying…" : "Retry now"}
            </Button>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="grid gap-3 md:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      )}
      {!isLoading && items.length === 0 && !isError && (
        <EmptyState
          title="No matching events"
          description={
            data && data.length > 0
              ? `Filter "${filter}" returns 0 of ${data.length} events. Try "All".`
              : "Trigger a new live event from the home page to populate the timeline."
          }
        />
      )}
      {!isLoading && items.length > 0 && (
        <>
          <p
            className="text-[10px] uppercase tracking-wider text-muted-foreground"
            aria-live="polite"
          >
            Showing {items.length} of {data?.length ?? 0} events
            {isFetching && <span className="ml-2 text-primary">· refreshing…</span>}
          </p>
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {items.map((e) => (
              <EventCard key={e.id} event={e} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
