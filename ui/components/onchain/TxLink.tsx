import { ExternalLink } from "lucide-react";
import { shortAddr } from "@/lib/utils";
import { arcTxUrl } from "@/lib/api";
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
  const href = url ?? arcTxUrl(txHash);
  return (
    <span className="inline-flex items-center gap-2">
      {label && (
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
      )}
      <a
        href={href}
        target="_blank"
        rel="noreferrer noopener"
        className="inline-flex items-center gap-1 rounded font-mono text-xs text-primary hover:underline focus:outline-none focus:ring-2 focus:ring-ring"
        aria-label={`View transaction ${display} on Arc explorer (opens in new tab)`}
      >
        {display}
        <ExternalLink className="h-3 w-3" aria-hidden />
      </a>
      <RealVsMockBadge mode={mode} className="text-[9px]" />
    </span>
  );
}
