// Polyglot Alpha v2 API client.
// Backend FastAPI lives at NEXT_PUBLIC_API_BASE (default http://localhost:8000).

// Window may be augmented at runtime with a runtime-override API base
// (e.g. by an injected <script> in the static shell).
interface PolyglotWindow extends Window {
  __POLYGLOT_API_BASE__?: string;
}

export const API_BASE =
  (typeof window !== "undefined" &&
    (window as PolyglotWindow).__POLYGLOT_API_BASE__) ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://localhost:8000";

async function safeJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export async function fetchEvents(): Promise<EventSummary[]> {
  const res = await fetch(`${API_BASE}/events`, { cache: "no-store" });
  return safeJson(res);
}

export async function fetchEvent(id: string): Promise<EventDetail> {
  const res = await fetch(`${API_BASE}/events/${id}`, { cache: "no-store" });
  return safeJson(res);
}

export async function fetchAgent(address: string): Promise<AgentProfile> {
  const res = await fetch(`${API_BASE}/agents/${address}`, { cache: "no-store" });
  return safeJson(res);
}

export async function fetchLeaderboard(): Promise<LeaderboardEntry[]> {
  const res = await fetch(`${API_BASE}/leaderboard`, { cache: "no-store" });
  return safeJson(res);
}

export async function triggerEvent(
  payload?: TriggerPayload,
): Promise<{ event_id: string }> {
  // Phase 1 RSS ingestion: the demo button drives the real Chinese-language
  // RSS pipeline (BBC zh / RFI Chinese / Xinhua / SCMP / People's Daily) +
  // Haiku triage, so the backend picks a fresh Polymarket-style question
  // from current news rather than serving a hardcoded fiscal-stimulus
  // string. A 5-min sliding-window dedup on the backend reuses event_id
  // for back-to-back clicks within 5 min; older duplicates are salted on
  // the server so they kick off a fresh lifecycle.
  const body: Record<string, unknown> =
    payload && Object.keys(payload).length > 0
      ? (payload as Record<string, unknown>)
      : {
          event_source: "rss",
          language: "zh",
          category: "macro",
          rss_window_minutes: 24 * 60,
          auction_window_seconds: 0.5,
        };
  const res = await fetch(`${API_BASE}/trigger/event`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return safeJson(res);
}

/**
 * Promote a dry-run Polymarket submission to a real submission.
 * Backend wires this to POST `/events/{id}/polymarket/submit-real`.
 * Returns the updated submission record.
 */
export async function submitPolymarketReal(
  eventId: string,
): Promise<{ market_id?: string; market_url?: string; is_simulated: boolean }> {
  const res = await fetch(
    `${API_BASE}/events/${eventId}/polymarket/submit-real`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_real_submission: true }),
    },
  );
  return safeJson(res);
}

// ─── Types ──────────────────────────────────────────────────────────────────

export type PhaseStatus = "pending" | "running" | "completed" | "failed";

export interface PhaseState {
  name: string;
  status: PhaseStatus;
  startedAt?: string;
  completedAt?: string;
  details?: Record<string, unknown>;
}

export interface EventSummary {
  id: string;
  headline: string;
  source: string;
  status: PhaseStatus | "live" | "historical";
  ingestedAt: string;
  mode: "live" | "mock" | "historical";
  marketSymbol?: string;
}

export interface BidEntry {
  agent: string;
  bid: number;
  reputation: number;
  winner?: boolean;
}

export interface JudgeScore {
  judge: string;
  score: number;
  weight?: number;
  category: "translation" | "style" | "alignment" | "private";
  hardGate?: boolean;
  notes?: string;
  passed?: boolean;
  threshold?: number;
}

export interface EventDetail extends EventSummary {
  phases: PhaseState[];
  bids?: BidEntry[];
  translation?: {
    source: string;
    target: string;
    debate: { analyst: string; argument: string; verdict?: string }[];
    synthesized: string;
    framings?: string[];
    layerDetails?: {
      layer: string;
      input?: string;
      output?: string;
      model?: string;
      durationMs?: number;
    }[];
  };
  judges?: JudgeScore[];
  overallVerdict?: "PASS" | "FAIL" | string;
  overallReasoning?: string;
  anchor?: {
    txHash: string;
    block?: number;
    explorerUrl: string;
    contractAddress?: string;
    ipfsCid?: string;
  };
  polymarket?: {
    submissionTx?: string;
    builderCode: string;
    marketUrl?: string;
    marketId?: string;
    mode?: "live" | "dry_run" | "mock" | "real" | "simulated";
    isSimulated?: boolean;
    status?: string;
    payload?: Record<string, unknown> | null;
    feesEstimateUsdc?: number | null;
    revenueStream: {
      ts: string | null;
      usd: number;
      recipient?: string;
      arcTxHash?: string;
      isSimulated?: boolean;
    }[];
    recentFills?: { ts: string; txHash: string; amountUsd: number }[];
  };
  // Backend may also expose these snake_case fields at the top level of
  // EventDetail as a fallback when `polymarket` is not yet populated.
  builder_code?: string;
  market_id?: string;
  market_url?: string;
  is_simulated?: boolean;
}

export interface AgentProfile {
  address: string;
  alias?: string;
  reputation: number;
  totalRevenue: number;
  wins: number;
  losses: number;
  history: { ts: string; reputation: number; revenue: number }[];
}

export interface LeaderboardEntry {
  rank: number;
  address: string;
  alias?: string;
  reputation: number;
  revenueUsd: number;
  winRate: number;
  // Extra fields returned by the v2 backend `/leaderboard` endpoint. The
  // primary leaderboard table uses the legacy camelCase fields above; the
  // operators page consumes the snake_case fields directly to surface
  // total bids / wins / cumulative builder fees as live, non-mock counts.
  total_bids?: number;
  total_wins?: number;
  avg_quality?: number;
  cumulative_fees?: number;
}

export interface TriggerPayload {
  source?: string;
  language?: string;
  headline?: string;
  event_source?: "rss" | "manual";
  title?: string;
  sources?: { name: string; url: string }[];
}

// ─── SSE event taxonomy ───────────────────────────────────────────────────
//
// The lifecycle now ships 13 named events. The original 10 ("core") drive the
// 7 top-level phases shown on the Timeline; the 3 new "debate" events fire
// inside phase 2 (Translation Pipeline) and animate the agent-debate
// sub-phase chips (L3 Critics → L4 Moderator → L5 Refine).
//
// We deliberately keep `SseEventType` referring to the 10 core events so the
// existing exhaustive `Record<SseEventType, …>` consumers (e.g. the trigger
// button's progress labels owned by agent ε) continue to typecheck without
// modification. New code should reach for `AnySseEventType` to cover all 13.

/** The 10 core lifecycle events (one per top-level phase transition). */
export type SseEventType =
  | "event.created"
  | "event.updated"
  | "auction.opened"
  | "bid.submitted"
  | "auction.settled"
  | "translation.completed"
  | "quality.verdict"
  | "onchain.committed"
  | "polymarket.submitted"
  | "builder_fee.accrued"
  | "event.finalized";

/** The 3 agent-debate sub-events inside phase 2 (Translation Pipeline). */
export type DebateSseEventType =
  | "critic.completed"
  | "moderator.verdict"
  | "refine.completed";

/** Union of every named SSE event the backend can emit (core + debate). */
export type AnySseEventType = SseEventType | DebateSseEventType;

export const SSE_EVENT_TYPES: AnySseEventType[] = [
  "event.created",
  "event.updated",
  "auction.opened",
  "bid.submitted",
  "auction.settled",
  "translation.completed",
  "critic.completed",
  "moderator.verdict",
  "refine.completed",
  "quality.verdict",
  "onchain.committed",
  "polymarket.submitted",
  "builder_fee.accrued",
  "event.finalized",
];

/** Maps every SSE event → 7-phase index. Debate events all live in phase 2. */
export const SSE_TO_PHASE_INDEX: Record<AnySseEventType, number> = {
  "event.created": 0,
  "event.updated": 0,
  "auction.opened": 1,
  "bid.submitted": 1,
  "auction.settled": 1,
  "translation.completed": 2,
  "critic.completed": 2,
  "moderator.verdict": 2,
  "refine.completed": 2,
  "quality.verdict": 3,
  "onchain.committed": 4,
  "polymarket.submitted": 5,
  "builder_fee.accrued": 6,
  "event.finalized": 6,
};

export const PHASE_NAMES: string[] = [
  "Event Ingestion",
  "USDC Auction",
  "Translation Pipeline",
  "11-Judge Panel",
  "On-chain Anchor",
  "Polymarket V2 Submission",
  "Streaming Revenue",
];

// ─── Sub-phase taxonomy (Translation Pipeline debate) ─────────────────────
//
// Phase 2 ("Translation Pipeline") fans out into five sub-phases that the
// Timeline renders as progressive-disclosure chips. Each sub-phase advances
// from `pending → running → completed` as named SSE events arrive.

/** Sub-phases nested under each top-level phase index. */
export const SUB_PHASES_BY_PHASE: Record<number, string[]> = {
  2: [
    "L1 Analysts",
    "L2 Translators",
    "L3 Critics",
    "L4 Moderator",
    "L5 Refine",
  ],
};

/**
 * Maps an incoming SSE event to the index of the sub-phase that should turn
 * "completed" when it fires. Undefined → the event has no sub-phase mapping.
 */
export const SSE_TO_SUB_PHASE_INDEX: Partial<Record<AnySseEventType, number>> = {
  // L1/L2 don't have dedicated SSE events yet — they're rolled into the
  // single `translation.completed` event which marks both as done.
  "translation.completed": 1, // L2 Translators done (and L1 implicitly)
  "critic.completed": 2, // L3 Critics
  "moderator.verdict": 3, // L4 Moderator
  "refine.completed": 4, // L5 Refine
};

// Arc explorer base — surfaced everywhere so TxLink can build links uniformly.
export const ARC_EXPLORER_BASE: string = "https://testnet.arcscan.app";

export function arcTxUrl(txHash: string): string {
  return `${ARC_EXPLORER_BASE}/tx/${txHash}`;
}

export function arcAddressUrl(address: string): string {
  return `${ARC_EXPLORER_BASE}/address/${address}`;
}
