"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Play, Check, Loader2 } from "lucide-react";
import { triggerEvent } from "@/lib/api";
import { useDemoMode } from "@/contexts/ModeContext";

// W16-A: button no longer waits for lifecycle terminal status before
// navigating. The detail page renders the phases in "pending" state
// immediately and animates them live via its own SSE subscription. This
// flips the perceived latency from 40+s (in dev mode) down to whatever the
// POST round-trip takes (~50-300ms). The progressive labels that used to
// drive the busy state are gone because they're invisible after we push.

const TRIGGER_LABEL_LIVE = "Trigger live demo";
const TRIGGER_LABEL_MOCK = "Trigger mock demo";
const TRIGGER_LABEL_BUSY = "Triggering…";
const TRIGGER_LABEL_SLOW = "Backend slow — still trying…";

// Soft hint timer: if the POST hasn't returned within 5s we swap the busy
// label to a "Backend slow…" reassurance. The user can still navigate away
// manually; we don't actually unblock or auto-redirect anywhere because
// without an event_id there's nothing to navigate to.
const SLOW_HINT_MS = 5_000;

// Hard fallback: if POST hangs for this long, give up and reset the button
// so the user can retry without reloading the page.
const POST_HARD_TIMEOUT_MS = 30_000;

export function TriggerButton() {
  const router = useRouter();
  const { mode, isHydrated } = useDemoMode();
  // W7-E: keep the label SSR-safe ("Trigger live demo") until the mode
  // context has hydrated so the server-rendered button text matches the
  // first client paint. After hydration the label switches to reflect the
  // resolved mode without producing a React hydration mismatch warning.
  const effectiveMode: typeof mode = isHydrated ? mode : "live";
  const [busy, setBusy] = useState(false);
  const [triggered, setTriggered] = useState(false);
  const [slow, setSlow] = useState(false);
  const [errorHint, setErrorHint] = useState<string | null>(null);
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hardTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clean up any leftover timers on unmount so they can't fire after
  // navigation.
  useEffect(() => {
    return () => {
      if (slowTimerRef.current) clearTimeout(slowTimerRef.current);
      if (hardTimerRef.current) clearTimeout(hardTimerRef.current);
    };
  }, []);

  const label = useMemo(() => {
    if (!busy) {
      if (triggered) return "Triggered";
      return effectiveMode === "mock" ? TRIGGER_LABEL_MOCK : TRIGGER_LABEL_LIVE;
    }
    return slow ? TRIGGER_LABEL_SLOW : TRIGGER_LABEL_BUSY;
  }, [busy, triggered, slow, effectiveMode]);

  const clearTimers = () => {
    if (slowTimerRef.current) {
      clearTimeout(slowTimerRef.current);
      slowTimerRef.current = null;
    }
    if (hardTimerRef.current) {
      clearTimeout(hardTimerRef.current);
      hardTimerRef.current = null;
    }
  };

  const onClick = async () => {
    setBusy(true);
    setTriggered(false);
    setSlow(false);
    setErrorHint(null);

    // W7-A fallback safety net — these timers only fire if the POST never
    // returns within their window. In the happy path (POST in 50-300ms) the
    // success branch clears them before they get a chance to run.
    slowTimerRef.current = setTimeout(() => setSlow(true), SLOW_HINT_MS);
    hardTimerRef.current = setTimeout(() => {
      // POST is wedged. Don't auto-navigate (no event id). Reset so the
      // user can retry. The slow label stays visible briefly as feedback.
      setBusy(false);
      setErrorHint(
        "Backend didn't respond in 30s — try again or see /events for cached runs.",
      );
    }, POST_HARD_TIMEOUT_MS);

    try {
      // Forward the user-selected demo mode (W5-B) so the backend can pick
      // the synthetic mock lifecycle vs. the real RSS pipeline. Pass the
      // resolved `mode` (hydrated context) — `effectiveMode` exists only
      // so the SSR/CSR labels match before hydration.
      const result = await triggerEvent(undefined, mode);
      clearTimers();
      if (result?.event_id) {
        // Push immediately. The detail page has its own SSE subscription
        // and renders phases in "pending" state until the first event
        // lands, so the user sees the DAG animate live instead of staring
        // at the trigger button.
        setTriggered(true);
        router.push(`/events/${String(result.event_id)}`);
        // Leave `busy=true` for a tick so the spinner stays visible until
        // Next.js commits the route transition. The component unmounts on
        // navigation anyway; the cleanup effect handles any stragglers.
        setBusy(false);
      } else {
        // Unexpected — POST succeeded but no id. Fall back to the events
        // list so the user can still find their run.
        setBusy(false);
        setTriggered(true);
        router.push(`/events`);
      }
    } catch {
      clearTimers();
      setBusy(false);
      setSlow(false);
      setErrorHint("Backend unreachable — see /events for cached runs.");
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <Button
        variant="outline"
        disabled={busy}
        onClick={onClick}
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
      {(busy || errorHint) && (
        <p
          className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground"
          aria-live="polite"
        >
          {errorHint ?? (slow ? TRIGGER_LABEL_SLOW : TRIGGER_LABEL_BUSY)}
        </p>
      )}
    </div>
  );
}
