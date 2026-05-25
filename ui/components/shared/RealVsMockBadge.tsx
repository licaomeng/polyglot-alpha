import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface Props {
  mode: "live" | "mock" | "historical" | string;
  className?: string;
}

export function RealVsMockBadge({ mode, className }: Props) {
  if (mode === "live") {
    return (
      <Badge variant="success" className={cn("gap-1.5", className)} aria-label="Live data">
        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
        Live
      </Badge>
    );
  }
  if (mode === "historical") {
    return (
      <Badge variant="info" className={cn("gap-1.5", className)} aria-label="Historical data">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-sky-400" />
        Historical
      </Badge>
    );
  }
  return (
    <Badge variant="warning" className={cn("gap-1.5", className)} aria-label="Mock data">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />
      Mock
    </Badge>
  );
}
