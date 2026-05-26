import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function shortAddr(addr?: string | null, head = 6, tail = 4): string {
  if (!addr) return "—";
  if (addr.length <= head + tail + 2) return addr;
  return `${addr.slice(0, head)}…${addr.slice(-tail)}`;
}

/**
 * Format a reputation score for display.
 *
 * Backend stores reputation as a raw decimal in [0, 1]. The UI surfaces it
 * uniformly as a whole-number percent (e.g. ``0.85`` → ``"85%"``) so the
 * leaderboard win-rate column and the bid/operator rep columns share one
 * convention. Pass ``rawDecimal=true`` only when the surrounding text is a
 * formula (e.g. ``max(reputation, 1.0)`` in the auction explainer) where the
 * 0–1 scale is mathematically required.
 */
export function formatReputation(
  value: number | null | undefined,
  options?: { rawDecimal?: boolean },
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (options?.rawDecimal) return value.toFixed(2);
  return `${Math.round(value * 100)}%`;
}

export function formatUsd(value: number | null | undefined, fractionDigits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(value);
}

export function formatNumber(value: number | null | undefined, fractionDigits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(value);
}

export function relativeTime(iso?: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}
