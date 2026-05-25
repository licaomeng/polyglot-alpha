import { ShieldCheck } from "lucide-react";

export function BuilderCodeBadge({ code }: { code: string }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md border border-fuchsia-500/30 bg-fuchsia-500/10 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-fuchsia-300"
      aria-label={`Polymarket builder code ${code}`}
    >
      <ShieldCheck className="h-3 w-3" aria-hidden />
      {code}
    </span>
  );
}
