"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Minimal CSS-only tooltip that opens on hover *and* keyboard focus (a11y).
 * We deliberately avoid pulling in `@radix-ui/react-tooltip` to keep the
 * bundle lean — the dep isn't installed in this project.
 *
 * Usage:
 *   <Tooltip content="What this means…">
 *     <button aria-label="more info">…</button>
 *   </Tooltip>
 */
export interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactElement;
  side?: "top" | "bottom";
  align?: "start" | "center" | "end";
  className?: string;
  /** Width of the tooltip bubble. Defaults to `max-w-xs`. */
  widthClassName?: string;
}

export function Tooltip({
  content,
  children,
  side = "top",
  align = "center",
  className,
  widthClassName = "max-w-xs",
}: TooltipProps) {
  const wrapperRef = React.useRef<HTMLSpanElement | null>(null);
  return (
    <span ref={wrapperRef} className="relative inline-flex group">
      {children}
      <span
        role="tooltip"
        className={cn(
          // `w-max` lets the tooltip claim its natural content width (rather
          // than shrinking to the wrapper span's ~16px column), capped by
          // `widthClassName` (`max-w-xs` by default) so long copy still wraps.
          "pointer-events-none absolute z-50 hidden w-max rounded-md border border-border/60 bg-popover px-3 py-2 text-[11px] leading-snug text-popover-foreground shadow-lg",
          "group-hover:block group-focus-within:block",
          widthClassName,
          side === "top" ? "bottom-full mb-2" : "top-full mt-2",
          align === "start"
            ? "left-0"
            : align === "end"
              ? "right-0"
              : "left-1/2 -translate-x-1/2",
          className,
        )}
      >
        {content}
      </span>
    </span>
  );
}
