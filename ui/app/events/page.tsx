"use client";

import { useState, useMemo } from "react";
import { EventCard } from "@/components/event/EventCard";
import { useEventList } from "@/hooks/useEventList";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/EmptyState";
import { Button } from "@/components/ui/button";
import { Search } from "lucide-react";

const FILTERS = ["all", "running", "completed", "live"] as const;

export default function EventsPage() {
  const { data, isLoading } = useEventList();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [query, setQuery] = useState("");

  const items = useMemo(() => {
    if (!data) return [];
    return data
      .filter((e) => (filter === "all" ? true : e.status === filter))
      .filter((e) =>
        query.trim().length === 0
          ? true
          : e.headline.toLowerCase().includes(query.toLowerCase()) ||
            e.source.toLowerCase().includes(query.toLowerCase()),
      );
  }, [data, filter, query]);

  return (
    <div className="container space-y-6 py-8">
      <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Events</h1>
          <p className="text-xs text-muted-foreground">
            Live + recent runs across the full 7-phase pipeline.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              className="h-9 w-56 rounded-md border border-input bg-background pl-7 pr-3 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
              placeholder="Search headlines…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search events"
            />
          </div>
          {FILTERS.map((f) => (
            <Button
              key={f}
              variant={f === filter ? "default" : "outline"}
              size="sm"
              onClick={() => setFilter(f)}
              aria-pressed={f === filter}
              className="capitalize"
            >
              {f}
            </Button>
          ))}
        </div>
      </header>

      {isLoading && (
        <div className="grid gap-3 md:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      )}
      {!isLoading && items.length === 0 && (
        <EmptyState
          title="No matching events"
          description="Try clearing filters or trigger a new live event from the home page."
        />
      )}
      {!isLoading && items.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {items.map((e) => (
            <EventCard key={e.id} event={e} />
          ))}
        </div>
      )}
    </div>
  );
}
