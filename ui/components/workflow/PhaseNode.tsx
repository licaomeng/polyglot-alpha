"use client";

import { Handle, Position } from "@xyflow/react";
import type { PhaseStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

interface NodeData {
  label: string;
  status?: PhaseStatus;
  index?: number;
  isActive?: boolean;
  phaseIndex?: number;
}

export function PhaseNode({ data }: { data: NodeData }) {
  const status = data.status ?? "pending";
  return (
    <div
      className={cn(
        "min-w-[200px] cursor-pointer rounded-lg border bg-card/95 px-3.5 py-3 shadow-sm transition-all",
        status === "running" && "border-primary/60 glow-cyan",
        status === "completed" && "border-emerald-500/40",
        status === "failed" && "border-destructive/50",
        status === "pending" && "border-border/60",
        data.isActive && "scale-105 border-accent ring-2 ring-accent/60",
      )}
      title="Click to jump to this phase in the timeline"
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !bg-primary/60" />
      <div className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">
        {typeof data.index === "number" ? `STEP ${data.index.toString().padStart(2, "0")}` : "STEP"}
      </div>
      <div className="mt-1 text-sm font-semibold leading-tight">{data.label}</div>
      <div className="mt-2 flex items-center gap-1.5">
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            status === "running" && "animate-pulse bg-primary",
            status === "completed" && "bg-emerald-400",
            status === "failed" && "bg-destructive",
            status === "pending" && "bg-muted-foreground/40",
          )}
        />
        <span className="text-xs capitalize text-muted-foreground">{status}</span>
      </div>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !bg-primary/60" />
    </div>
  );
}
