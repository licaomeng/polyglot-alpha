"use client";

import { useEffect, useState } from "react";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { Clock } from "lucide-react";

/**
 * Animated per-phase progress bar with an "elapsed / estimated remaining"
 * label. Drives off `startedAt` + a typical-duration estimate (passed in).
 *
 * - elapsed: re-evaluated every 1s while the phase is running
 * - estimated remaining: max(0, typicalMs - elapsedMs)
 * - the bar fills proportionally (cap at 99% while running so it never looks
 *   stuck at "done" before the SSE event lands)
 */
export interface ProgressIndicatorProps {
  phaseName: string;
  status: "pending" | "running" | "completed" | "failed";
  startedAt?: string;
  completedAt?: string;
  /** Typical phase duration in seconds (defaults to 30s). */
  typicalSeconds?: number;
  className?: string;
}

function fmt(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s === 0 ? `${m}m` : `${m}m${s}s`;
}

export function ProgressIndicator({
  phaseName,
  status,
  startedAt,
  completedAt,
  typicalSeconds = 30,
  className,
}: ProgressIndicatorProps) {
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    if (status !== "running") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [status]);

  if (status === "pending") {
    return (
      <div
        className={cn(
          "flex items-center gap-2 text-[11px] text-muted-foreground",
          className,
        )}
      >
        <Clock className="h-3 w-3" aria-hidden />
        <span className="font-mono">
          {phaseName} · queued — typical ~{fmt(typicalSeconds)}
        </span>
      </div>
    );
  }

  const startMs = startedAt ? new Date(startedAt).getTime() : null;
  const endMs = completedAt ? new Date(completedAt).getTime() : null;

  if (status === "completed") {
    // Always short-circuit when the phase is completed — never fall through
    // to the "running" branch which would render a misleading "X remaining"
    // label even though the phase is finished.
    const elapsed =
      startMs && endMs ? Math.max(0, (endMs - startMs) / 1000) : null;
    return (
      <div
        className={cn(
          "flex items-center gap-2 text-[11px] text-emerald-300/80",
          className,
        )}
      >
        <Clock className="h-3 w-3" aria-hidden />
        <span className="font-mono">
          {phaseName} · completed{elapsed !== null ? ` in ${fmt(elapsed)}` : ""}
        </span>
      </div>
    );
  }

  if (status === "failed") {
    return (
      <div
        className={cn(
          "flex items-center gap-2 text-[11px] text-destructive",
          className,
        )}
      >
        <Clock className="h-3 w-3" aria-hidden />
        <span className="font-mono">{phaseName} · failed</span>
      </div>
    );
  }

  // running
  const elapsedSec = startMs ? Math.max(0, (now - startMs) / 1000) : 0;
  const remainingSec = Math.max(0, typicalSeconds - elapsedSec);
  const pct = Math.min(99, (elapsedSec / typicalSeconds) * 100);

  return (
    <div
      className={cn("space-y-1", className)}
      aria-label={`${phaseName} progress`}
    >
      <div className="flex items-center justify-between gap-3 text-[11px]">
        <span className="inline-flex items-center gap-1.5 font-mono text-foreground/90">
          <Clock className="h-3 w-3 text-primary" aria-hidden />
          {phaseName} · {fmt(elapsedSec)} elapsed
        </span>
        <span className="font-mono text-muted-foreground">
          ~{fmt(remainingSec)} remaining
        </span>
      </div>
      <Progress value={pct} tone="primary" />
    </div>
  );
}
