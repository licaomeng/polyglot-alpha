"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchEvents, type EventSummary } from "@/lib/api";

// v2: backend DB holds the source of truth (78+ real events). No mock fallback.
// If the API is unreachable we propagate the error so callers can render an
// empty/error state — not stale fake data.
export function useEventList() {
  return useQuery<EventSummary[]>({
    queryKey: ["events"],
    queryFn: fetchEvents,
    refetchInterval: 5000,
    retry: 1,
  });
}
