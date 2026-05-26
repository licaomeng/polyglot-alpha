import { ShieldCheck } from "lucide-react";

// Render a builder code (potentially a 0x-prefixed 64-char hex) as a compact
// truncated form `0xabcd…1234` for readability while keeping the full value
// in the aria-label and title for accessibility / hover-to-inspect.
function formatBuilderCode(code: string): string {
  const trimmed = code.trim();
  if (trimmed.startsWith("0x") && trimmed.length > 14) {
    return `${trimmed.slice(0, 6)}…${trimmed.slice(-4)}`;
  }
  return trimmed;
}

export function BuilderCodeBadge({ code }: { code: string }) {
  const display = formatBuilderCode(code);
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md border border-fuchsia-500/30 bg-fuchsia-500/10 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-fuchsia-300"
      aria-label={`Polymarket builder code ${code}`}
      title={code}
    >
      <ShieldCheck className="h-3 w-3" aria-hidden />
      {display}
    </span>
  );
}
