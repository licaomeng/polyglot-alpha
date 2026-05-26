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
  mode?: "live" | "mock",
): Promise<{ event_id: string }> {
  // Phase 1 RSS ingestion: the demo button drives the real Chinese-language
  // RSS pipeline (BBC zh / RFI Chinese / Xinhua / SCMP / People's Daily) +
  // Haiku triage, so the backend picks a fresh Polymarket-style question
  // from current news rather than serving a hardcoded fiscal-stimulus
  // string. A 5-min sliding-window dedup on the backend reuses event_id
  // for back-to-back clicks within 5 min; older duplicates are salted on
  // the server so they kick off a fresh lifecycle.
  //
  // `mode` (W5-B) is forwarded to the backend so the user's selected
  // demo-mode (synthetic ~5-10s mock vs real ~60-90s lifecycle) takes
  // effect on the next trigger. Defaults to "live" when omitted.
  const base: Record<string, unknown> =
    payload && Object.keys(payload).length > 0
      ? (payload as Record<string, unknown>)
      : {
          event_source: "rss",
          language: "zh",
          category: "macro",
          rss_window_minutes: 24 * 60,
          auction_window_seconds: 0.5,
        };
  const body: Record<string, unknown> = { ...base, mode: mode ?? "live" };
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

/** Per-judge dossier surfaced by `/events/{id}.judges`. */
export interface JudgeDossierEntry {
  name: string;
  passed: boolean;
  score: number;
  reason: string;
  panelBudgetExceeded: boolean;
  softSkip: boolean;
  timeout: boolean;
  panelPartial: boolean;
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
  judges?: JudgeDossierEntry[] | JudgeScore[];
  /** True when the panel returned partial verdicts (budget exceeded). */
  panelPartial?: boolean;
  /** Judges that hit the panel-budget timeout (INSUFFICIENT_DATA). */
  pendingJudgeNames?: string[];
  /**
   * W9-A: on-chain JudgePanel.recordAttestation result. The γ-strategy
   * stamps a single aggregate verdict per event (keccak256 of the full
   * 11-judge dossier JSON + scaled overall_score). Live mode populates
   * ``txHash`` with the real Arc tx; mock mode emits the ``0xsim_*``
   * sentinel so the UI mutes the arcscan link.
   */
  judgesAttestation?: {
    txHash: string | null;
    attestationHash: string | null;
    scoreScaled: number | null;
    aggregatorAddress: string | null;
    registerTx?: string | null;
    strategy?: string;
  } | null;
  translation_scores?: Record<string, unknown> | null;
  style_alignment_passes?: Record<string, boolean> | null;
  verdict?: string;
  overall_score?: number | null;
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

// ─── Operator-facing endpoints (W9-C) ─────────────────────────────────────
//
// Both `claim-fees` and `register` accept an optional `mode: "mock" | "live"`
// body field. When `mode === "mock"` the backend skips real chain RPC and
// returns synthetic `0xsim_…` tx hashes; the local DB is still mutated so
// the UI can render the result without burning testnet gas.

export const SUPPORTED_OPERATOR_LANGUAGES = [
  "zh",
  "ru",
  "es",
  "ja",
  "ar",
  "en",
] as const;
export type OperatorLanguage = (typeof SUPPORTED_OPERATOR_LANGUAGES)[number];

export interface PendingFeesResponse {
  operator_address: string;
  pending_usdc: number;
  event_count: number;
}

export interface ClaimFeesResponse {
  success: boolean;
  tx_hash: string | null;
  amount_claimed_usdc: number;
  is_simulated: boolean;
  operator_address: string;
}

export interface RegisterOperatorRequest {
  operator_address: string;
  display_name: string;
  model_label?: string;
  languages?: OperatorLanguage[];
  stake_amount_usdc?: number;
  mode?: "live" | "mock";
  signature?: string;
}

export interface RegisterOperatorResponse {
  operator_address: string;
  status: string;
  stake_tx: string | null;
  reputation_tx: string | null;
  initial_reputation: number;
  auction_stream_url: string;
  display_name: string;
  registration_id: string | null;
  is_simulated: boolean;
  success: boolean;
}

/** Fetch claimable builder-fee balance for an operator wallet. */
export async function fetchOperatorPendingFees(
  address: string,
): Promise<PendingFeesResponse> {
  const res = await fetch(
    `${API_BASE}/api/operators/${address}/pending-fees`,
    { cache: "no-store" },
  );
  return safeJson(res);
}

/** Settle (withdraw) accumulated builder fees for an operator wallet. */
export async function claimOperatorFees(
  address: string,
  mode: "live" | "mock" = "live",
): Promise<ClaimFeesResponse> {
  const res = await fetch(
    `${API_BASE}/api/operators/${address}/claim-fees`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    },
  );
  return safeJson(res);
}

/** Register a new external operator with anti-Sybil stake. */
export async function registerOperator(
  payload: RegisterOperatorRequest,
): Promise<RegisterOperatorResponse> {
  const res = await fetch(`${API_BASE}/api/operators/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return safeJson(res);
}

// ─── Withdraw stake (W9-F) ──────────────────────────────────────────────────

export interface StakeStatusResponse {
  operator_address: string;
  staked: boolean;
  amount_usdc: number;
  /** Unix epoch seconds (or block height in future); null when not locked. */
  locked_until_block: number | null;
  can_withdraw: boolean;
}

export interface WithdrawStakeResponse {
  success: boolean;
  tx_hash: string | null;
  amount_recovered_usdc: number;
  is_simulated: boolean;
  operator_address: string;
}

/** Read the operator's auction-contract stake status (live or DB-derived). */
export async function fetchOperatorStakeStatus(
  address: string,
): Promise<StakeStatusResponse> {
  const res = await fetch(
    `${API_BASE}/api/operators/${address}/stake-status`,
    { cache: "no-store" },
  );
  return safeJson(res);
}

/**
 * Withdraw the operator's unlocked auction stake. Defaults to ``mock`` so
 * the demo path never burns real testnet gas; pass ``mode="live"`` with a
 * ``private_key`` only when the user has explicitly opted in.
 */
export async function withdrawOperatorStake(
  address: string,
  mode: "live" | "mock" = "mock",
  privateKey?: string,
): Promise<WithdrawStakeResponse> {
  const body: Record<string, unknown> = { mode };
  if (mode === "live" && privateKey) {
    body.private_key = privateKey;
  }
  const res = await fetch(
    `${API_BASE}/api/operators/${address}/withdraw-stake`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  // Special handling so the UI can distinguish 404 (no_stake) and 409
  // (locked) from generic 5xx failures via the thrown Error message.
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    const detailStr =
      typeof detail === "string" ? detail : JSON.stringify(detail);
    throw new Error(`API ${res.status}: ${detailStr}`);
  }
  return (await res.json()) as WithdrawStakeResponse;
}
