"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchEvent, type EventDetail } from "@/lib/api";

// v2: no mock fallback — backend DB is the source of truth.
export function useEvent(id: string) {
  return useQuery<EventDetail | undefined>({
    queryKey: ["event", id],
    queryFn: () => fetchEvent(id),
    enabled: Boolean(id),
    // Stop polling once we know the event doesn't exist (404). Otherwise a
    // stale tab open at /events/{missing-id} hammers the backend with
    // `GET /events/{id}` every 4s forever (and `GET /sse/events?event_id=…`
    // via useEventStream). For 5xx / network errors we keep polling so the
    // page recovers when the backend comes back.
    refetchInterval: (query) => {
      const err = query.state.error as Error | null;
      if (err && err.message.includes("404")) return false;
      return 4000;
    },
    // Same logic for background refetch — don't keep polling a 404.
    refetchIntervalInBackground: true,
    // Don't retry on 404 — the id is genuinely missing and won't appear.
    retry: (failureCount, err: unknown) => {
      const msg = err instanceof Error ? err.message : "";
      if (msg.includes("404")) return false;
      return failureCount < 1;
    },
  });
}
