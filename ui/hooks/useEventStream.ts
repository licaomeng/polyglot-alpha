"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  API_BASE,
  PHASE_NAMES,
  SSE_EVENT_TYPES,
  SSE_TO_PHASE_INDEX,
  type AnySseEventType,
  type PhaseState,
  type PhaseStatus,
} from "@/lib/api";

/** Last-seen SSE event surfaced to UI consumers (drives trigger button labels). */
export interface LatestSseEvent {
  type: AnySseEventType | "hello" | "heartbeat";
  data: Record<string, unknown>;
  receivedAt: number;
}

interface UseEventStreamReturn {
  phases: PhaseState[] | undefined;
  connected: boolean;
  latest: LatestSseEvent | null;
  /** All events received this session, newest-last. Capped at 200. */
  history: LatestSseEvent[];
}

const MAX_HISTORY = 200;

function nextStatus(prev: PhaseStatus | undefined, incoming: PhaseStatus): PhaseStatus {
  // "completed" and "failed" are sticky against earlier "running"/"pending".
  if (prev === "completed" && incoming === "running") return "completed";
  if (prev === "failed") return "failed";
  return incoming;
}

function applyEvent(
  current: PhaseState[] | undefined,
  type: AnySseEventType,
  data: Record<string, unknown>,
): PhaseState[] {
  // Initialise a baseline 7-phase scaffold the first time we receive any event.
  const base: PhaseState[] =
    current && current.length === PHASE_NAMES.length
      ? [...current]
      : PHASE_NAMES.map((name) => ({ name, status: "pending" as PhaseStatus }));

  const idx = SSE_TO_PHASE_INDEX[type];
  if (idx === undefined) return base;

  const now = new Date().toISOString();

  // Phases strictly before the active one become "completed".
  for (let i = 0; i < idx; i += 1) {
    base[i] = {
      ...base[i],
      status: nextStatus(base[i].status, "completed"),
      completedAt: base[i].completedAt ?? now,
    };
  }

  // Phase-specific status transitions.
  if (type === "event.created") {
    // The event row is freshly created with a placeholder title — RSS poll
    // + Haiku scoring are still happening in the background. Show phase 0
    // (Event Ingestion) as RUNNING with the placeholder label so the user
    // sees the news-fetch step animate. `event.updated` will flip it to
    // completed once the real title + scoring metadata land.
    base[idx] = {
      ...base[idx],
      status: nextStatus(base[idx].status, "running"),
      startedAt: base[idx].startedAt ?? now,
      details: { ...(base[idx].details ?? {}), ...data },
    };
  } else if (type === "event.updated") {
    // RSS poll + Haiku scoring done — real title + sources + scoring
    // metadata arrived. Mark phase 0 completed and merge new data into
    // phase details so the page can re-render the title.
    base[idx] = {
      ...base[idx],
      status: nextStatus(base[idx].status, "completed"),
      startedAt: base[idx].startedAt ?? now,
      completedAt: now,
      details: { ...(base[idx].details ?? {}), ...data },
    };
  } else if (type === "auction.opened" || type === "bid.submitted") {
    base[idx] = {
      ...base[idx],
      status: nextStatus(base[idx].status, "running"),
      startedAt: base[idx].startedAt ?? now,
      details: { ...(base[idx].details ?? {}), ...data },
    };
  } else if (type === "auction.settled") {
    base[idx] = {
      ...base[idx],
      status: nextStatus(base[idx].status, "completed"),
      startedAt: base[idx].startedAt ?? now,
      completedAt: now,
      details: { ...(base[idx].details ?? {}), ...data },
    };
  } else if (type === "translation.completed") {
    // Phase 2 stays "running" while debate sub-phases (critic / moderator /
    // refine) fire; we only mark it "completed" once `refine.completed`
    // arrives. If the backend skips the debate layers entirely (legacy
    // pipeline), the absence of those events leaves phase 2 in "running"
    // until `quality.verdict` arrives and progresses past it — matching
    // pre-debate behaviour.
    const prevSub =
      (base[idx].details?.subPhases as Record<string, PhaseStatus> | undefined) ?? {};
    base[idx] = {
      ...base[idx],
      status: nextStatus(base[idx].status, "running"),
      startedAt: base[idx].startedAt ?? now,
      details: {
        ...(base[idx].details ?? {}),
        ...data,
        subPhases: {
          ...prevSub,
          "L1 Analysts": "completed",
          "L2 Translators": "completed",
          "L3 Critics": prevSub["L3 Critics"] ?? "running",
        },
      },
    };
  } else if (
    type === "critic.completed" ||
    type === "moderator.verdict" ||
    type === "refine.completed"
  ) {
    const prevSub =
      (base[idx].details?.subPhases as Record<string, PhaseStatus> | undefined) ?? {};
    const nextSub: Record<string, PhaseStatus> = { ...prevSub };
    if (type === "critic.completed") {
      nextSub["L1 Analysts"] = "completed";
      nextSub["L2 Translators"] = "completed";
      nextSub["L3 Critics"] = "completed";
      nextSub["L4 Moderator"] = "running";
    } else if (type === "moderator.verdict") {
      nextSub["L3 Critics"] = "completed";
      nextSub["L4 Moderator"] = "completed";
      nextSub["L5 Refine"] = "running";
    } else {
      nextSub["L4 Moderator"] = "completed";
      nextSub["L5 Refine"] = "completed";
    }
    const phase2Completed = type === "refine.completed";
    base[idx] = {
      ...base[idx],
      status: phase2Completed
        ? nextStatus(base[idx].status, "completed")
        : nextStatus(base[idx].status, "running"),
      startedAt: base[idx].startedAt ?? now,
      completedAt: phase2Completed ? now : base[idx].completedAt,
      details: {
        ...(base[idx].details ?? {}),
        ...data,
        subPhases: nextSub,
      },
    };
  } else if (type === "quality.verdict") {
    const verdict = String(data.verdict ?? "");
    const lifecycleRejected = verdict !== "" && verdict !== "PASS";
    // Judge phase ran successfully — it produced a verdict. The phase
    // itself is "completed" regardless of which way the verdict went.
    // This matches the REST /events snapshot (phase 4 status=completed,
    // top-level status=REJECTED) and is what the user sees when they
    // reload the page after lifecycle terminates.
    base[idx] = {
      ...base[idx],
      status: "completed",
      completedAt: now,
      details: { ...(base[idx].details ?? {}), ...data },
    };
    // If the judges rejected, the downstream phases (Anchor / Polymarket /
    // Streaming) are failed at the lifecycle level even though the backend
    // still emits onchain.committed / polymarket.submitted signals for
    // bookkeeping. Pre-mark them "failed" so the sticky `nextStatus` rule
    // (failed-wins) keeps them visually correct when those follow-up
    // events arrive.
    if (lifecycleRejected) {
      for (let i = idx + 1; i < base.length; i += 1) {
        base[i] = { ...base[i], status: "failed", completedAt: now };
      }
    }
  } else if (type === "onchain.committed") {
    base[idx] = {
      ...base[idx],
      status: nextStatus(base[idx].status, "completed"),
      completedAt: now,
      details: { ...(base[idx].details ?? {}), ...data },
    };
  } else if (type === "polymarket.submitted") {
    base[idx] = {
      ...base[idx],
      status: nextStatus(base[idx].status, "completed"),
      completedAt: now,
      details: { ...(base[idx].details ?? {}), ...data },
    };
  } else if (type === "builder_fee.accrued") {
    base[idx] = {
      ...base[idx],
      status: nextStatus(base[idx].status, "running"),
      startedAt: base[idx].startedAt ?? now,
      details: { ...(base[idx].details ?? {}), ...data },
    };
  } else if (type === "event.finalized") {
    // `event.finalized` only flips the phase to "completed" when the
    // lifecycle ended cleanly. If the judges already marked this phase
    // failed (because verdict ≠ PASS), the sticky-failed rule in
    // nextStatus keeps it failed — matching what the REST /events
    // endpoint returns and the user-visible REJECTED badge.
    base[idx] = {
      ...base[idx],
      status: nextStatus(base[idx].status, "completed"),
      completedAt: now,
      details: { ...(base[idx].details ?? {}), ...data },
    };
  }

  return base;
}

/**
 * Subscribe to the backend SSE stream and reduce all 10 lifecycle event types
 * into a 7-phase array. Also surfaces `latest` and `history` so trigger flows
 * can display progress labels driven by named events.
 *
 * Backend uses sse-starlette which emits **named** events via the `event:`
 * field; the default `onmessage` handler only catches anonymous messages, so
 * we register listeners for each named type explicitly.
 */
export function useEventStream(eventId?: string): UseEventStreamReturn {
  const [phases, setPhases] = useState<PhaseState[] | undefined>(undefined);
  const [connected, setConnected] = useState(false);
  const [latest, setLatest] = useState<LatestSseEvent | null>(null);
  const [history, setHistory] = useState<LatestSseEvent[]>([]);
  const filterEventId = useRef<string | undefined>(eventId);
  filterEventId.current = eventId;

  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = eventId
      ? `${API_BASE}/sse/events?event_id=${encodeURIComponent(eventId)}`
      : `${API_BASE}/sse/events`;

    let source: EventSource | null = null;
    try {
      source = new EventSource(url);
    } catch {
      return;
    }
    const es = source;

    const onOpen = () => setConnected(true);
    const onError = () => setConnected(false);
    es.addEventListener("open", onOpen);
    es.addEventListener("error", onError);

    const handle = (type: AnySseEventType | "hello" | "heartbeat", raw: string) => {
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(raw || "{}") as Record<string, unknown>;
      } catch {
        return;
      }
      // Optional eventId filter — backend may broadcast all events on one stream.
      if (
        filterEventId.current &&
        data.event_id !== undefined &&
        String(data.event_id) !== String(filterEventId.current)
      ) {
        return;
      }
      const entry: LatestSseEvent = { type, data, receivedAt: Date.now() };
      setLatest(entry);
      setHistory((prev) => {
        const next = [...prev, entry];
        return next.length > MAX_HISTORY ? next.slice(next.length - MAX_HISTORY) : next;
      });
      if (type !== "hello" && type !== "heartbeat") {
        setPhases((prev) => applyEvent(prev, type, data));
      }
    };

    const allTypes: Array<AnySseEventType | "hello" | "heartbeat"> = [
      ...SSE_EVENT_TYPES,
      "hello",
      "heartbeat",
    ];
    const namedListeners: Array<{
      type: AnySseEventType | "hello" | "heartbeat";
      fn: (ev: MessageEvent<string>) => void;
    }> = allTypes.map((t) => ({
      type: t,
      fn: (ev: MessageEvent<string>) => handle(t, ev.data ?? ""),
    }));
    namedListeners.forEach(({ type, fn }) =>
      es.addEventListener(type as string, fn as EventListener),
    );

    // Fallback for anonymous `message` events (legacy shape with {phase, phases}).
    const onAnonymous = (ev: MessageEvent<string>) => {
      try {
        const data = JSON.parse(ev.data ?? "{}") as {
          phase?: PhaseState;
          phases?: PhaseState[];
        };
        if (data.phases) setPhases(data.phases);
        else if (data.phase) {
          setPhases((prev) => {
            const list = prev ? [...prev] : [];
            const idx = list.findIndex((p) => p.name === data.phase!.name);
            if (idx >= 0) list[idx] = data.phase!;
            else list.push(data.phase!);
            return list;
          });
        }
      } catch {
        // ignore
      }
    };
    es.addEventListener("message", onAnonymous as EventListener);

    return () => {
      namedListeners.forEach(({ type, fn }) =>
        es.removeEventListener(type as string, fn as EventListener),
      );
      es.removeEventListener("message", onAnonymous as EventListener);
      es.removeEventListener("open", onOpen);
      es.removeEventListener("error", onError);
      es.close();
    };
  }, [eventId]);

  return useMemo(
    () => ({ phases, connected, latest, history }),
    [phases, connected, latest, history],
  );
}
