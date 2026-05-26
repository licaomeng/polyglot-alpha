import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { EventStatusBadge } from "./EventStatusBadge";
import { RealVsMockBadge } from "@/components/shared/RealVsMockBadge";
import type { EventSummary } from "@/lib/api";
import { relativeTime } from "@/lib/utils";
import { ArrowUpRight } from "lucide-react";

export function EventCard({ event }: { event: EventSummary }) {
  return (
    <Link
      href={`/events/${event.id}`}
      className="group block focus:outline-none"
      aria-label={`Open event ${event.headline}`}
    >
      <Card className="h-full transition-all duration-200 group-hover:-translate-y-0.5 group-hover:border-primary/40 group-hover:shadow-lg group-hover:shadow-primary/10 group-focus-visible:border-primary group-focus-visible:ring-2 group-focus-visible:ring-ring">
        <CardContent className="p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <RealVsMockBadge mode={event.mode} />
              <EventStatusBadge status={event.status} />
              {event.marketSymbol && (
                <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  {event.marketSymbol}
                </span>
              )}
            </div>
            <ArrowUpRight
              className="h-4 w-4 text-muted-foreground transition-all duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-primary"
              aria-hidden
            />
          </div>
          <h3
            className={
              event.headline
                ? "mt-3 text-base font-semibold leading-snug text-balance"
                : "mt-3 text-base font-semibold italic leading-snug text-muted-foreground"
            }
          >
            {event.headline || "(no headline)"}
          </h3>
          <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
            <span>{event.source || <span className="italic opacity-70">unknown source</span>}</span>
            <span className="font-mono">{relativeTime(event.ingestedAt)}</span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
