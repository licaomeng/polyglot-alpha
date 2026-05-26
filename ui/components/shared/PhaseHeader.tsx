import { Badge } from "@/components/ui/badge";
import type { PhaseStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<PhaseStatus, string> = {
  pending: "Pending",
  running: "Running",
  completed: "Done",
  failed: "Failed",
};

// Failed (system error: timeout, crash, infra) renders grey/muted — distinct
// from "destructive" red which is reserved for quality verdicts (Rejected).
const STATUS_VARIANT: Record<
  PhaseStatus,
  "secondary" | "info" | "success" | "muted"
> = {
  pending: "secondary",
  running: "info",
  completed: "success",
  failed: "muted",
};

export function PhaseHeader({
  index,
  title,
  status,
  description,
  className,
}: {
  index?: number;
  title: string;
  status: PhaseStatus;
  description?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-3", className)}>
      <div className="flex items-start gap-3">
        {typeof index === "number" && (
          <span className="grid h-7 w-7 flex-shrink-0 place-items-center rounded-md border border-border bg-secondary/40 font-mono text-xs">
            {index.toString().padStart(2, "0")}
          </span>
        )}
        <div className="min-w-0">
          <h3 className="text-sm font-semibold leading-tight">{title}</h3>
          {description && (
            <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
          )}
        </div>
      </div>
      <Badge variant={STATUS_VARIANT[status]} className="flex-shrink-0">
        {STATUS_LABEL[status]}
      </Badge>
    </div>
  );
}
