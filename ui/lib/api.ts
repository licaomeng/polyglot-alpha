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
  // v2 default: send a unique user_payload per click so the 24h content_hash
  // dedup doesn't 409 back-to-back demo clicks. The backend's event_source=rss
  // path ignores client-provided salt and always returns the same RSS-cached
  // headline, so the demo button drives the real 4-LLM + Arc TX + dry_run
  // Polymarket lifecycle via a freshly-timestamped user payload instead.
  const ts = Date.now();
  const body: Record<string, unknown> =
    payload && Object.keys(payload).length > 0
      ? (payload as Record<string, unknown>)
      : {
          title: `Live demo · Will Beijing announce major fiscal stimulus before December 2026? [${ts}]`,
          sources: [
            { name: "xinhua", url: `https://www.xinhua.com/macro/${ts}` },
            { name: "caixin", url: `https://www.caixin.com/${ts}` },
          ],
          language: "zh",
          category: "macro",
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
    mode?: "live" | "dry_run" | "mock";
    isSimulated?: boolean;
    status?: string;
    payload?: Record<string, unknown>;
    revenueStream: { ts: string; usd: number }[];
    recentFills?: { ts: string; txHash: string; amountUsd: number }[];
  };
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
}

export interface TriggerPayload {
  source?: string;
  language?: string;
  headline?: string;
  event_source?: "rss" | "manual";
  title?: string;
  sources?: { name: string; url: string }[];
}

// ─── SSE event taxonomy (10 lifecycle types) ──────────────────────────────

export type SseEventType =
  | "event.created"
  | "auction.opened"
  | "bid.submitted"
  | "auction.settled"
  | "translation.completed"
  | "quality.verdict"
  | "onchain.committed"
  | "polymarket.submitted"
  | "builder_fee.accrued"
  | "event.finalized";

export const SSE_EVENT_TYPES: SseEventType[] = [
  "event.created",
  "auction.opened",
  "bid.submitted",
  "auction.settled",
  "translation.completed",
  "quality.verdict",
  "onchain.committed",
  "polymarket.submitted",
  "builder_fee.accrued",
  "event.finalized",
];

// Maps SSE event types → 7-phase index (0-based against `_PHASE_NAMES`).
export const SSE_TO_PHASE_INDEX: Record<SseEventType, number> = {
  "event.created": 0,
  "auction.opened": 1,
  "bid.submitted": 1,
  "auction.settled": 1,
  "translation.completed": 2,
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

// Arc explorer base — surfaced everywhere so TxLink can build links uniformly.
export const ARC_EXPLORER_BASE: string = "https://testnet.arcscan.app";

export function arcTxUrl(txHash: string): string {
  return `${ARC_EXPLORER_BASE}/tx/${txHash}`;
}

export function arcAddressUrl(address: string): string {
  return `${ARC_EXPLORER_BASE}/address/${address}`;
}
