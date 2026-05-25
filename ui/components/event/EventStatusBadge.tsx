import { Badge } from "@/components/ui/badge";
import type { EventSummary } from "@/lib/api";

const MAP: Record<string, { label: string; variant: "info" | "warning" | "success" | "secondary" | "destructive" }> = {
  live: { label: "LIVE", variant: "info" },
  running: { label: "Running", variant: "info" },
  pending: { label: "Queued", variant: "secondary" },
  completed: { label: "Settled", variant: "success" },
  failed: { label: "Failed", variant: "destructive" },
  historical: { label: "Historical", variant: "secondary" },
};

export function EventStatusBadge({ status }: { status: EventSummary["status"] }) {
  const m = MAP[status] ?? { label: status, variant: "secondary" as const };
  return <Badge variant={m.variant}>{m.label}</Badge>;
}
