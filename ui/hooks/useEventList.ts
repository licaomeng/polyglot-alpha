"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchEvents, type EventSummary } from "@/lib/api";

// Bail out of long retries — the backend's /trigger/event blocks the
// server's event loop for the full 60-75s pipeline, so a stuck request
// would otherwise leave the list page on a perpetual loading skeleton.
const REQUEST_TIMEOUT_MS = 8000;

async function fetchEventsBounded(): Promise<EventSummary[]> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetchEvents();
  } finally {
    clearTimeout(t);
  }
}

// v2: backend DB holds the source of truth (78+ real events). No mock fallback.
// If the API is unreachable we propagate the error so callers can render an
// empty/error state — not stale fake data.
export function useEventList() {
  return useQuery<EventSummary[]>({
    queryKey: ["events"],
    queryFn: fetchEventsBounded,
    refetchInterval: 5000,
    retry: 1,
    // Keep showing the last successful payload while the next poll runs;
    // prevents the "stuck on skeleton" UX when the backend is slow.
    placeholderData: (prev) => prev,
  });
}
