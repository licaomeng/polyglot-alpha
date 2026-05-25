import { Lock } from "lucide-react";

export function ClosedIPCallout({ inline = false }: { inline?: boolean }) {
  if (inline) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider text-fuchsia-300">
        <Lock className="h-3 w-3" aria-hidden /> closed-IP
      </span>
    );
  }
  return (
    <div className="rounded-md border border-fuchsia-500/30 bg-fuchsia-500/5 p-2 text-[11px] text-fuchsia-200/90">
      <span className="font-medium text-fuchsia-300">Closed IP — </span>
      Judge weights and prompt internals are intentionally not exposed.
    </div>
  );
}
