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
