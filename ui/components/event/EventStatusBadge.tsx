import { Badge } from "@/components/ui/badge";
import type { EventSummary } from "@/lib/api";
import { statusInfo } from "@/lib/status";

/**
 * Render a coloured pill for the event status.
 *
 * The backend emits uppercase canonical statuses (`PENDING`, `SUBMITTED`,
 * `EVALUATING`, `REJECTED`, `FAILED`, etc.); historical SSE replay still
 * emits lowercase short-hand (`running` / `live`). Both flow through
 * `statusInfo` (single source of truth in `lib/status.ts`) so the badge
 * always shows a friendly label and a tone that actually matches the phase
 * — rather than falling back to raw SCREAMING_SNAKE_CASE — and the
 * underlying canonical enum stays available as a `title` tooltip and
 * `aria-label` for screen readers.
 */
export function EventStatusBadge({ status }: { status: EventSummary["status"] }) {
  const info = statusInfo(status);
  return (
    <Badge
      variant={info.variant}
      title={`Backend status: ${status}`}
      aria-label={`Status: ${info.label} (${status})`}
    >
      {info.label}
    </Badge>
  );
}
