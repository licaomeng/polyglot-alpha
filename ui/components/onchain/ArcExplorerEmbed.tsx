import { ExternalLink } from "lucide-react";

export function ArcExplorerEmbed({ txHash, url }: { txHash: string; url?: string }) {
  const href = url ?? `https://arc-explorer.example/tx/${txHash}`;
  return (
    <div className="rounded-md border border-border/60 bg-secondary/30 p-3 text-xs">
      <div className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
        Arc explorer
      </div>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 font-mono text-primary hover:underline"
      >
        Open in explorer
        <ExternalLink className="h-3 w-3" aria-hidden />
      </a>
    </div>
  );
}
