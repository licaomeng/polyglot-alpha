"use client";

import { useCallback, useRef } from "react";
import { cn } from "@/lib/utils";
import { useDemoMode, type DemoMode } from "@/contexts/ModeContext";

const MODES: { value: DemoMode; label: string }[] = [
  { value: "live", label: "LIVE" },
  { value: "mock", label: "MOCK" },
];

/**
 * Two-segment radiogroup that switches the global demo mode between `live`
 * and `mock`. The selected segment is colored (cyan for live, amber for
 * mock); the unselected segment fades to a neutral card-background swatch.
 *
 * Behavior:
 *   - Click writes to `localStorage` via `setMode`, never to the URL.
 *   - Arrow keys cycle between segments (W3C radiogroup pattern).
 *   - Width is fixed via `w-[148px]` on the outer wrapper so clicking the
 *     toggle never reflows the surrounding header.
 */
export function DemoModeToggle({ className }: { className?: string }) {
  const { mode, setMode } = useDemoMode();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const handleKey = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>, idx: number) => {
      // W3C radiogroup keyboard pattern: arrow keys move focus + selection,
      // space/enter activate the focused option. Home/End jump to extremes.
      const last = MODES.length - 1;
      let target: number | null = null;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        target = idx === last ? 0 : idx + 1;
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        target = idx === 0 ? last : idx - 1;
      } else if (e.key === "Home") {
        target = 0;
      } else if (e.key === "End") {
        target = last;
      } else if (e.key === " " || e.key === "Enter") {
        setMode(MODES[idx].value);
        e.preventDefault();
        return;
      }
      if (target !== null) {
        e.preventDefault();
        const next = MODES[target];
        setMode(next.value);
        refs.current[target]?.focus();
      }
    },
    [setMode],
  );

  return (
    <div
      role="radiogroup"
      aria-label="Demo mode"
      className={cn(
        "inline-flex h-8 w-[148px] shrink-0 items-stretch overflow-hidden rounded-md border border-border/60 bg-card text-[11px] font-mono uppercase tracking-wider",
        className,
      )}
    >
      {MODES.map((m, idx) => {
        const active = mode === m.value;
        const isLive = m.value === "live";
        return (
          <button
            key={m.value}
            ref={(el) => {
              refs.current[idx] = el;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            onClick={() => setMode(m.value)}
            onKeyDown={(e) => handleKey(e, idx)}
            className={cn(
              "flex flex-1 items-center justify-center px-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active && isLive && "bg-primary text-primary-foreground",
              active &&
                !isLive &&
                "border border-amber-500/40 bg-amber-500/30 text-amber-300",
              !active && "bg-card text-muted-foreground hover:text-foreground",
              // Subtle divider between the two segments so they read as a
              // single segmented control rather than two stacked buttons.
              idx === 0 && "border-r border-border/60",
            )}
          >
            {m.label}
          </button>
        );
      })}
    </div>
  );
}
