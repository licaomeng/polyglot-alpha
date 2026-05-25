"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchEvent, type EventDetail } from "@/lib/api";

// v2: no mock fallback — backend DB is the source of truth.
export function useEvent(id: string) {
  return useQuery<EventDetail | undefined>({
    queryKey: ["event", id],
    queryFn: () => fetchEvent(id),
    enabled: Boolean(id),
    refetchInterval: 4000,
    retry: 1,
  });
}
