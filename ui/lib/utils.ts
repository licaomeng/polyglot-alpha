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
 *
 * NOTE (W14-D): The on-chain `ReputationRegistry.sol` EMA has a known unit-
 * scale bug — `_fillSignal` stays pinned at the 0.5 floor for any realistic
 * fee, so this value is currently *not* informative as a primary UX metric.
 * Operator/leaderboard surfaces have switched to `formatWinsBids()` as the
 * primary display; this helper still drives the auction-explainer formula
 * and any "advanced / on-chain raw" detail panels.
 */
export function formatReputation(
  value: number | null | undefined,
  options?: { rawDecimal?: boolean },
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (options?.rawDecimal) return value.toFixed(2);
  return `${Math.round(value * 100)}%`;
}

/**
 * Format a wins-over-bids ratio for display (W14-D primary UX metric).
 *
 * Returns a string like ``"12/47 · 26%"`` for `wins=12, totalBids=47`.
 * Falls back to ``"—"`` when either input is missing/NaN. When totalBids
 * is 0 we render ``"0/0"`` without a percent (no auctions entered).
 *
 * This is the demo-safe replacement for the on-chain EMA reputation badge:
 * the EMA suffers from a known `_fillSignal` unit-scale bug in
 * `ReputationRegistry.sol` (stuck at the 0.5 floor), so we lead with the
 * unambiguous, off-chain wins/bids count and relegate the raw EMA to an
 * "advanced" detail row with an explainer tooltip.
 */
export function formatWinsBids(
  wins: number | null | undefined,
  totalBids: number | null | undefined,
): string {
  if (
    wins === null ||
    wins === undefined ||
    Number.isNaN(wins) ||
    totalBids === null ||
    totalBids === undefined ||
    Number.isNaN(totalBids)
  ) {
    return "—";
  }
  if (totalBids === 0) return "0/0";
  const pct = Math.round((wins / totalBids) * 100);
  return `${wins}/${totalBids} · ${pct}%`;
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

/**
 * Determine whether a raw `anchor.ipfsCid` / pipeline_trace_ipfs / reasoning_ipfs
 * value is a *real* IPFS content identifier we can route through a public gateway,
 * vs. a synthetic/mock provenance path emitted by the backend (e.g.
 * `ipfs://pipeline/qwen/59fac6348e57`). The latter must NOT render as a clickable
 * gateway link because the gateway will 404.
 *
 * Real CIDs:
 *   - v0:  `Qm` + 44 base58 chars
 *   - v1:  `bafy` + 55 base32 chars (most common; other multibase prefixes exist
 *          but in this codebase the live anchor flow emits `bafy…`)
 *
 * Anything else — including `ipfs://pipeline/...`, `ipfs://mock/...`,
 * `ipfs://synthetic/...`, or a bare placeholder string — should be displayed as
 * a muted, non-clickable provenance label.
 *
 * Returns `{ cid, isReal, gatewayUrl }` where:
 *   - `cid`        is the input with any `ipfs://` scheme prefix stripped
 *   - `isReal`     true if `cid` matches the v0/v1 CID shape above
 *   - `gatewayUrl` populated only when `isReal` is true
 */
export function classifyIpfsRef(raw?: string | null): {
  cid: string;
  isReal: boolean;
  gatewayUrl?: string;
} | null {
  if (!raw) return null;
  const cid = raw.replace(/^ipfs:\/\//, "");
  if (!cid) return null;
  const v0 = /^Qm[1-9A-HJ-NP-Za-km-z]{44}$/;
  const v1 = /^bafy[a-zA-Z0-9]{55,}$/;
  // A bare CID has no path separators. Synthetic refs like
  // `pipeline/qwen/abc` contain slashes and must be rejected.
  const isReal = !cid.includes("/") && (v0.test(cid) || v1.test(cid));
  return {
    cid,
    isReal,
    gatewayUrl: isReal ? `https://ipfs.io/ipfs/${cid}` : undefined,
  };
}

/**
 * Detect a synthetic ("sim-prefix") Arc tx hash emitted by the backend in
 * mock mode (W5-A2). Real Arc testnet tx hashes are `0x` + 64 hex chars;
 * synthetic mock hashes always begin with the literal prefix `0xsim_`.
 *
 * The UI uses this gate at every external-explorer link site so a synthetic
 * hash is rendered as muted, non-clickable text rather than wrapped in an
 * `https://testnet.arcscan.app/tx/0xsim_…` link that would 404.
 */
export function isSimTxHash(h: string | null | undefined): boolean {
  return typeof h === "string" && h.toLowerCase().startsWith("0xsim_");
}

/**
 * Detect a synthetic Polymarket market_id emitted by the backend in mock /
 * dry-run mode. The backend uses two prefixes: `sim-` for fully simulated
 * markets and `dryrun-` for dry-run submissions. Either prefix means the
 * market does not exist on polymarket.com and the UI MUST NOT wrap it in an
 * external link.
 */
export function isSimPolymarketId(id: string | null | undefined): boolean {
  if (typeof id !== "string") return false;
  const lower = id.toLowerCase();
  return lower.startsWith("sim-") || lower.startsWith("dryrun-");
}

/**
 * Build a Polymarket market URL when, and only when, the supplied id is a
 * real (non-sim) market_id. Returns `null` for sim / dry-run ids so callers
 * can fall back to a muted, non-clickable text label.
 */
export function polymarketMarketUrl(id: string | null | undefined): string | null {
  if (!id || isSimPolymarketId(id)) return null;
  return `https://polymarket.com/market/${id}`;
}

/**
 * Validate a `market_url` field returned by the backend. The backend may
 * synthesise a `polymarket.com/market/sim-...` URL in mock mode; we don't
 * trust it blindly. Returns the URL only when it's a real polymarket URL
 * (i.e. the market_id segment does NOT start with `sim-` / `dryrun-`); for
 * any sim-prefixed URL, returns `null` so the UI renders muted text.
 */
export function safePolymarketUrl(url: string | null | undefined): string | null {
  if (typeof url !== "string" || !url) return null;
  const match = url.match(/\/market\/([^/?#]+)/);
  if (match && isSimPolymarketId(match[1])) return null;
  return url;
}

/**
 * Build an Arc testnet explorer URL for a tx hash when, and only when, the
 * hash is a *real* on-chain hash. Synthetic `0xsim_…` hashes return `null`
 * so callers can render a muted span instead of a broken external link.
 *
 * The base URL is the canonical Arc testnet explorer used by `arcTxUrl()`
 * in `ui/lib/api.ts`; we don't import from there to avoid a cycle and to
 * keep this helper self-contained for unit testing.
 */
export function arcscanTxUrl(h: string | null | undefined): string | null {
  if (!h || isSimTxHash(h)) return null;
  return `https://testnet.arcscan.app/tx/${h}`;
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
