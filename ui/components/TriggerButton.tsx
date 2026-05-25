"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Play, Check, Loader2 } from "lucide-react";
import { triggerEvent, type SseEventType } from "@/lib/api";
import { useEventStream } from "@/hooks/useEventStream";

// Lifecycle phase → human-readable progress label. Keys are the named SSE
// event types so the hook can drive UI directly from server emissions.
const PROGRESS_LABELS: Record<SseEventType, string> = {
  "event.created": "Fetching latest non-English news…",
  "auction.opened": "Opening Arc auction (60s window)…",
  "bid.submitted": "Bids arriving from 4 translator agents…",
  "auction.settled": "Auction settled — winner selected",
  "translation.completed": "11-judge panel evaluating…",
  "quality.verdict": "Anchoring proof on Arc testnet…",
  "onchain.committed": "Submitting to Polymarket (dry_run)…",
  "polymarket.submitted": "Streaming builder fees…",
  "builder_fee.accrued": "Streaming builder fees…",
  "event.finalized": "Done — navigating to event detail…",
};

const FALLBACK_INITIAL_LABEL = "Fetching latest non-English news…";

export function TriggerButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [triggered, setTriggered] = useState(false);
  // Track the event id returned by POST so the SSE filter can be re-applied
  // even after the button has finished loading.
  const [eventId, setEventId] = useState<string | undefined>(undefined);
  // Snapshot of latest SSE type (used to drive the progress label). We avoid
  // resubscribing to the SSE stream in this component — the page-level
  // provider already opens one — but a transient subscription here is fine
  // since it auto-closes on unmount.
  const { latest } = useEventStream(eventId);
  const [progressLabel, setProgressLabel] = useState<string | null>(null);
  const navigateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!busy) return;
    if (!latest) return;
    const fromLifecycle =
      latest.type !== "hello" && latest.type !== "heartbeat"
        ? PROGRESS_LABELS[latest.type as SseEventType]
        : undefined;
    if (fromLifecycle) setProgressLabel(fromLifecycle);
  }, [latest, busy]);

  useEffect(() => {
    if (!busy) return;
    if (latest?.type === "event.finalized" && eventId) {
      // Slight delay so the user sees the "Done" label before the route change.
      navigateTimerRef.current = setTimeout(() => {
        router.push(`/events/${eventId}`);
        setBusy(false);
        setTriggered(true);
      }, 800);
    }
    return () => {
      if (navigateTimerRef.current) clearTimeout(navigateTimerRef.current);
    };
  }, [busy, latest, eventId, router]);

  const label = useMemo(() => {
    if (!busy) return triggered ? "Triggered" : "Trigger live demo";
    return progressLabel ?? FALLBACK_INITIAL_LABEL;
  }, [busy, triggered, progressLabel]);

  return (
    <div className="flex flex-col gap-2">
      <Button
        variant="outline"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setTriggered(false);
          setProgressLabel(FALLBACK_INITIAL_LABEL);
          try {
            const result = await triggerEvent();
            if (result?.event_id) {
              setEventId(String(result.event_id));
              // Navigate immediately so the user watches the 60-75s lifecycle
              // progress on the event-detail page rather than staring at a
              // spinner button. The detail page's own useEventStream hook
              // drives the Timeline animation in real time.
              router.push(`/events/${result.event_id}`);
              setBusy(false);
              setTriggered(true);
            } else {
              // Backend returned without an id — fall back to events list.
              setTimeout(() => {
                router.push(`/events`);
                setBusy(false);
                setTriggered(true);
              }, 600);
            }
          } catch {
            // Backend down or rate-limited — surface error state.
            setProgressLabel("Backend unreachable — see /events for cached runs.");
            setTimeout(() => {
              setBusy(false);
            }, 1500);
          }
        }}
        aria-label="Trigger a live demo event"
      >
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden />
        ) : triggered ? (
          <Check className="h-4 w-4 text-emerald-400" aria-hidden />
        ) : (
          <Play className="h-4 w-4" aria-hidden />
        )}
        {label}
      </Button>
      {busy && (
        <p
          className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground"
          aria-live="polite"
        >
          {progressLabel ?? FALLBACK_INITIAL_LABEL}
        </p>
      )}
    </div>
  );
}
