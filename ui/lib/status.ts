// Centralized event-status taxonomy.
//
// The backend exposes a wider set of canonical statuses (PENDING / AUCTION_OPEN
// / AUCTION_SETTLED / TRANSLATING / EVALUATING / REJECTED / COMMITTED /
// SUBMITTED / FAILED) than the UI cares about visually. Several pages used to
// hand-code a small lowercase whitelist (e.g. ``running/completed/failed``)
// which silently dropped real rows because the live data uses uppercase
// canonical names. This module is the single source of truth so the events
// list, history page, filter buttons, and status badge all agree on:
//
//   - Which backend status maps into which UI bucket
//   - The human-readable label and badge variant for every bucket
//   - The display label shown for the raw canonical status (tooltip / cell)
//
// Keeping the mapping declarative means a new backend status is a single
// addition here rather than touching three pages.

import type { BadgeProps } from "@/components/ui/badge";

/** Canonical statuses surfaced by the backend `EventStatus` Python enum. */
export const CANONICAL_STATUSES = [
  "PENDING",
  "AUCTION_OPEN",
  "AUCTION_SETTLED",
  "TRANSLATING",
  "EVALUATING",
  "REJECTED",
  "COMMITTED",
  "SUBMITTED",
  "FAILED",
] as const;

export type CanonicalStatus = (typeof CANONICAL_STATUSES)[number];

/**
 * UI-level buckets. These map 1:N to canonical statuses and are the values the
 * filter buttons in `/events` and the dropdown in `/history` actually toggle.
 */
export type StatusBucket = "all" | "pending" | "running" | "completed" | "failed";

export const STATUS_BUCKETS: StatusBucket[] = [
  "all",
  "pending",
  "running",
  "completed",
  "failed",
];

/**
 * Human-friendly label + tone (variant) for the badge that renders a raw
 * canonical status. Keys are uppercase canonical values; lowercase legacy
 * values from the SSE event-summary stream (``live``/``historical``) are
 * folded back in for backward compatibility.
 */
const STATUS_DISPLAY: Record<
  string,
  { label: string; variant: NonNullable<BadgeProps["variant"]>; bucket: StatusBucket }
> = {
  // Canonical (uppercase) — what `/events` returns today.
  PENDING: { label: "Queued", variant: "secondary", bucket: "pending" },
  AUCTION_OPEN: { label: "Auctioning", variant: "info", bucket: "running" },
  AUCTION_SETTLED: { label: "Settled bid", variant: "info", bucket: "running" },
  TRANSLATING: { label: "Translating", variant: "info", bucket: "running" },
  EVALUATING: { label: "Judging", variant: "warning", bucket: "running" },
  COMMITTED: { label: "Anchored", variant: "success", bucket: "completed" },
  SUBMITTED: { label: "Settled", variant: "success", bucket: "completed" },
  // Rejected = quality-panel verdict ⇒ destructive red. Failed = system /
  // pipeline crash ⇒ muted grey so reviewers can visually tell them apart in
  // the events list and the page header.
  REJECTED: { label: "Rejected", variant: "destructive", bucket: "failed" },
  FAILED: { label: "Failed", variant: "muted", bucket: "failed" },

  // Legacy lowercase strings still emitted by some code paths.
  live: { label: "LIVE", variant: "info", bucket: "running" },
  running: { label: "Running", variant: "info", bucket: "running" },
  pending: { label: "Queued", variant: "secondary", bucket: "pending" },
  completed: { label: "Settled", variant: "success", bucket: "completed" },
  failed: { label: "Failed", variant: "muted", bucket: "failed" },
  historical: { label: "Historical", variant: "secondary", bucket: "completed" },
};

const FALLBACK = {
  label: "Unknown",
  variant: "secondary" as const,
  bucket: "all" as StatusBucket,
};

/** Resolve a raw status (any casing, lowercase or uppercase) to display info. */
export function statusInfo(raw: string | null | undefined): {
  label: string;
  variant: NonNullable<BadgeProps["variant"]>;
  bucket: StatusBucket;
} {
  if (!raw) return FALLBACK;
  return STATUS_DISPLAY[raw] ?? STATUS_DISPLAY[raw.toUpperCase()] ?? {
    label: String(raw),
    variant: "secondary" as const,
    bucket: "all" as StatusBucket,
  };
}

/** Bucket → human label for filter buttons. */
export const BUCKET_LABEL: Record<StatusBucket, string> = {
  all: "All",
  pending: "Queued",
  running: "Running",
  completed: "Settled",
  failed: "Failed",
};

/**
 * Tooltip blurb explaining what each bucket contains — used in filter UI so
 * evaluators understand the mapping rather than guessing which raw enum maps
 * where.
 */
export const BUCKET_TOOLTIP: Record<StatusBucket, string> = {
  all: "Every event regardless of phase.",
  pending: "Events still queued before the USDC auction opens (PENDING).",
  running:
    "Events currently in flight — auction, translation, or 11-judge evaluation phases.",
  completed:
    "Events that passed the judge panel and reached Arc commit or Polymarket submission.",
  failed:
    "Events that were rejected by the judge panel or failed before submission.",
};

/** Test whether an event with the given raw status belongs to the bucket. */
export function bucketMatches(raw: string | null | undefined, bucket: StatusBucket): boolean {
  if (bucket === "all") return true;
  return statusInfo(raw).bucket === bucket;
}
