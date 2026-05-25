import { Lock } from "lucide-react";
import { cn } from "@/lib/utils";

export function PrivateIPCallout({
  message = "Evaluator weights and prompt internals are intentionally redacted.",
  className,
}: {
  message?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-2.5 rounded-lg border border-fuchsia-500/30 bg-fuchsia-500/5 p-3 text-xs text-fuchsia-200/90",
        className,
      )}
      role="note"
    >
      <Lock className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-fuchsia-400" aria-hidden />
      <span>
        <span className="font-medium text-fuchsia-300">Closed IP — </span>
        {message}
      </span>
    </div>
  );
}
