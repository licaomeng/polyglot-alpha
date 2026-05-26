"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Lightweight progress bar — shadcn-compatible API surface (`value` 0..100)
 * but renders as a plain styled `<div>` to avoid adding `@radix-ui/react-progress`
 * as a runtime dependency.
 */
export interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 0–100 percentage. Clamped on render. */
  value?: number;
  /** Optional indeterminate state (continuous shimmer). */
  indeterminate?: boolean;
  /** Tone — defaults to cyan/primary; use `success` for emerald. */
  tone?: "primary" | "success" | "warning" | "danger";
}

const TONE_MAP: Record<NonNullable<ProgressProps["tone"]>, string> = {
  primary: "bg-primary",
  success: "bg-emerald-400",
  warning: "bg-amber-400",
  danger: "bg-destructive",
};

export function Progress({
  value = 0,
  indeterminate = false,
  tone = "primary",
  className,
  ...rest
}: ProgressProps) {
  const clamped = Math.min(100, Math.max(0, value));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : clamped}
      className={cn(
        "relative h-1.5 w-full overflow-hidden rounded-full bg-muted/40",
        className,
      )}
      {...rest}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-500 ease-out",
          TONE_MAP[tone],
          indeterminate && "animate-pulse",
        )}
        style={{ width: indeterminate ? "100%" : `${clamped}%` }}
      />
    </div>
  );
}
