"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchLeaderboard, type LeaderboardEntry } from "@/lib/api";

// v2: no mock fallback — backend DB is the source of truth.
export function useLeaderboard() {
  return useQuery<LeaderboardEntry[]>({
    queryKey: ["leaderboard"],
    queryFn: fetchLeaderboard,
    retry: 1,
  });
}
