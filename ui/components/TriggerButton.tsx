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
  "event.updated": "News cluster scored — opening auction…",
  "auction.opened": "Opening Arc auction (60s window)…",
  "bid.submitted": "Bids arriving from 3 reference seeders…",
  "auction.settled": "Auction settled — winner selected",
  "translation.completed": "11-judge panel evaluating…",
  "quality.verdict": "Anchoring proof on Arc testnet…",
  "onchain.committed": "Submitting to Polymarket (dry_run)…",
  "polymarket.submitted": "Streaming builder fees…",
  "builder_fee.accrued": "Streaming builder fees…",
  "event.finalized": "Done — navigating to event detail…",
};

const FALLBACK_INITIAL_LABEL = "Fetching latest non-English news…";

// Hard cap on how long the trigger button waits for SSE before navigating
// anyway. The lifecycle typically completes in 60-90s; a generous cap keeps
// the UI responsive if SSE is throttled or the user's network is flaky.
const NAVIGATE_FALLBACK_MS = 120_000;

export function TriggerButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [triggered, setTriggered] = useState(false);
  // Track the event id returned by POST so the SSE filter can be re-applied
  // even after the button has finished loading.
  const [eventId, setEventId] = useState<string | undefined>(undefined);
  // Only subscribe to SSE once we have an event_id — opening an unfiltered
  // stream before the POST returns causes a churn (close-and-reopen with
  // filter) that drops the early ``event.created`` event, which is why the
  // progressive labels appeared frozen on "Triggered" in Wave 1.
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

  // Navigate + clear busy when the lifecycle finalizes (or fallback timer
  // fires). This keeps the button mounted long enough for the progressive
  // labels to actually animate through the SSE event types.
  useEffect(() => {
    if (!busy || !eventId) return;
    if (latest?.type === "event.finalized") {
      if (navigateTimerRef.current) {
        clearTimeout(navigateTimerRef.current);
        navigateTimerRef.current = null;
      }
      setBusy(false);
      setTriggered(true);
      router.push(`/events/${eventId}`);
    }
  }, [latest, busy, eventId, router]);

  // NOTE: removed the legacy `event.finalized → router.push` effect that used
  // to do a delayed redirect to the triggered event. The click handler already
  // navigates immediately after the POST returns (line 78+), and this effect
  // was hijacking the URL whenever ANY event finalized while the user was
  // browsing /operators / /leaderboard / etc. — see G1 finding C1.

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
          // Backend pre-creates the event row and schedules the 60-90s
          // lifecycle in a BackgroundTask, so the POST returns event_id in
          // ~10 ms. We stay on the trigger page so progressive SSE labels
          // animate the button text; the effect above navigates once
          // `event.finalized` lands (or after NAVIGATE_FALLBACK_MS).
          try {
            const result = await triggerEvent();
            if (result?.event_id) {
              const newId = String(result.event_id);
              setEventId(newId);
              if (navigateTimerRef.current) {
                clearTimeout(navigateTimerRef.current);
              }
              navigateTimerRef.current = setTimeout(() => {
                setBusy(false);
                setTriggered(true);
                router.push(`/events/${newId}`);
              }, NAVIGATE_FALLBACK_MS);
            } else {
              router.push(`/events`);
              setBusy(false);
              setTriggered(true);
            }
          } catch {
            setProgressLabel(
              "Backend unreachable — see /events for cached runs.",
            );
            setBusy(false);
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
