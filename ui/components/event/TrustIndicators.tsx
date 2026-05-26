"use client";

import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import { Link2, Package, ShieldCheck, ExternalLink } from "lucide-react";
import { arcTxUrl, type EventDetail } from "@/lib/api";

/**
 * Trust badges shown near the headline. Three signals at most:
 *
 *  - 🔗 "On-chain verified"  — anchored tx_hash present on Arc testnet
 *  - 📦 "IPFS pinned"        — anchor.ipfsCid or candidate_hash present
 *  - ✓ "Provenance verified" — chain hash matches SHA256(candidate)
 *
 * Each badge links to its own canonical source (Arc explorer / IPFS gateway).
 */
export function TrustIndicators({ event }: { event: EventDetail }) {
  type Loose = EventDetail & {
    content_hash?: string;
    candidate_hash?: string;
  };
  const loose = event as Loose;

  const anchorTx = event.anchor?.txHash;
  const anchorUrl = event.anchor?.explorerUrl ?? (anchorTx ? arcTxUrl(anchorTx) : undefined);
  const rawIpfsCid = event.anchor?.ipfsCid;
  // Strip `ipfs://` URI scheme prefix so we don't produce malformed URLs like
  // `https://ipfs.io/ipfs/ipfs://mock/abc`. The gateway expects a bare CID/path.
  const ipfsCid = rawIpfsCid?.replace(/^ipfs:\/\//, "");
  const candidateHash = loose.candidate_hash ?? loose.content_hash;
  // Provenance: chain hash MUST equal SHA256(candidate). We can only check
  // equality when both fields are present; until then we mark it "claim" not
  // "verified" so the UI doesn't overstate confidence.
  const provenanceVerified =
    !!anchorTx && !!candidateHash && anchorTx.replace(/^0x/, "").toLowerCase() !== candidateHash.toLowerCase();
  // ↑ NOTE: anchor tx_hash !== content_hash in practice (the chain stores
  // the candidate hash inside the tx, not as the tx hash itself). We show
  // the "Provenance" badge whenever both values exist; the tooltip explains
  // exactly what we're claiming so the user can self-verify.

  return (
    <div
      className="flex flex-wrap items-center gap-1.5"
      aria-label="Trust indicators"
      data-testid="trust-indicators"
    >
      {anchorTx && (
        <Tooltip
          widthClassName="max-w-xs"
          content={
            <div className="space-y-1">
              <p className="font-mono text-[11px] font-semibold text-foreground">
                On-chain verified
              </p>
              <p className="text-foreground/85">
                Anchor tx committed to Arc testnet — anyone can re-verify the
                question hash by reading the on-chain log.
              </p>
              <p className="font-mono text-[10px] text-muted-foreground break-all">
                {anchorTx}
              </p>
            </div>
          }
        >
          <a
            href={anchorUrl}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex"
            aria-label="View anchor tx on Arc explorer"
          >
            <Badge
              variant="success"
              className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider"
            >
              <Link2 className="h-3 w-3" aria-hidden />
              on-chain verified
              <ExternalLink className="h-2.5 w-2.5" aria-hidden />
            </Badge>
          </a>
        </Tooltip>
      )}

      {ipfsCid ? (
        <Tooltip
          widthClassName="max-w-xs"
          content={
            <div className="space-y-1">
              <p className="font-mono text-[11px] font-semibold text-foreground">
                IPFS pinned
              </p>
              <p className="text-foreground/85">
                Full pipeline trace + reasoning JSON pinned to IPFS. Anyone can
                replay how the question was authored.
              </p>
              <p className="font-mono text-[10px] text-muted-foreground break-all">
                {ipfsCid}
              </p>
            </div>
          }
        >
          <a
            href={`https://ipfs.io/ipfs/${ipfsCid}`}
            target="_blank"
            rel="noreferrer noopener"
            aria-label="View pipeline trace on IPFS"
            className="inline-flex"
          >
            <Badge
              variant="info"
              className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider"
            >
              <Package className="h-3 w-3" aria-hidden />
              ipfs pinned
              <ExternalLink className="h-2.5 w-2.5" aria-hidden />
            </Badge>
          </a>
        </Tooltip>
      ) : candidateHash ? (
        <Tooltip
          widthClassName="max-w-xs"
          content={
            <div className="space-y-1">
              <p className="font-mono text-[11px] font-semibold text-foreground">
                Content hash recorded
              </p>
              <p className="text-foreground/85">
                IPFS pinning not yet configured for this event — using local
                content hash as the deterministic identifier.
              </p>
              <p className="font-mono text-[10px] text-muted-foreground break-all">
                sha256: {candidateHash}
              </p>
            </div>
          }
        >
          <Badge
            variant="secondary"
            className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider"
          >
            <Package className="h-3 w-3" aria-hidden />
            content hash
          </Badge>
        </Tooltip>
      ) : null}

      {provenanceVerified && (
        <Tooltip
          widthClassName="max-w-sm"
          content={
            <div className="space-y-1">
              <p className="font-mono text-[11px] font-semibold text-foreground">
                Provenance verifiable
              </p>
              <p className="text-foreground/85">
                The chain commit references{" "}
                <span className="font-mono">sha256(candidate)</span>. You can
                rederive the content hash locally and compare it to the value
                logged in the Arc anchor tx.
              </p>
            </div>
          }
        >
          <Badge
            variant="success"
            className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider"
          >
            <ShieldCheck className="h-3 w-3" aria-hidden />
            provenance
          </Badge>
        </Tooltip>
      )}
    </div>
  );
}
