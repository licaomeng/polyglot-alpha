import { ExternalLink } from "lucide-react";
import { shortAddr, isSimTxHash, arcscanTxUrl } from "@/lib/utils";
import { RealVsMockBadge } from "@/components/shared/RealVsMockBadge";

interface Props {
  txHash: string;
  url?: string;
  /** Tag the link as live (real Arc TX) vs mock/historical. */
  mode?: "live" | "mock" | "historical";
  label?: string;
}

export function TxLink({ txHash, url, mode = "live", label }: Props) {
  const display = shortAddr(txHash, 10, 6);
  // Gate external link: synthetic `0xsim_…` tx hashes (mock mode) never get
  // wrapped in an Arc explorer link — the explorer would 404 and the badge
  // would visually contradict itself. Render as muted text instead.
  const sim = isSimTxHash(txHash);
  const href = sim ? null : (url ?? arcscanTxUrl(txHash));
  return (
    <span className="inline-flex items-center gap-2">
      {label && (
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
      )}
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex min-h-[44px] items-center gap-1 rounded px-2 py-1 font-mono text-xs text-primary hover:underline focus:outline-none focus:ring-2 focus:ring-ring sm:min-h-0 sm:px-0 sm:py-0"
          aria-label={`View transaction ${display} on Arc explorer (opens in new tab)`}
        >
          {display}
          <ExternalLink className="h-3 w-3" aria-hidden />
        </a>
      ) : (
        <span
          className="inline-flex items-center gap-1 px-2 py-1 font-mono text-xs text-muted-foreground sm:px-0 sm:py-0"
          title="Synthetic tx — not on-chain"
          aria-label={`Synthetic transaction ${display} (not on-chain)`}
        >
          {display}
        </span>
      )}
      <RealVsMockBadge mode={mode} className="text-[9px]" />
    </span>
  );
}
