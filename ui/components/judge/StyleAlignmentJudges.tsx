"use client";

import type { JudgeScore } from "@/lib/api";
import { Star, ChevronDown, Shield } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { AnimatePresence, motion } from "framer-motion";

// Hard gates per spec §5.30: D1 (factual), D5 (UMA-dispute prevention),
// D8 (canonical) must pass or the entire panel fails.
const HARD_GATE_KEYS = new Set(["D1", "D5", "D8"]);
const UMA_KEY = "D5";

function isHardGate(judge: JudgeScore): boolean {
  if (judge.hardGate) return true;
  const upper = judge.judge.toUpperCase();
  for (const k of HARD_GATE_KEYS) if (upper.includes(k)) return true;
  return false;
}

function isUma(judge: JudgeScore): boolean {
  return judge.judge.toUpperCase().includes(UMA_KEY);
}

export function StyleAlignmentJudges({ judges }: { judges: JudgeScore[] }) {
  const grid = judges.filter((j) => j.category === "style" || j.category === "alignment");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (!grid.length) return null;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
          Style + Alignment (D1–D8){" "}
          <span className="ml-1 text-fuchsia-300">
            <Star className="inline h-2.5 w-2.5" aria-hidden /> hard gate
          </span>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {grid.map((j) => {
          const hard = isHardGate(j);
          const passed = j.passed ?? j.score >= 0.75;
          const uma = isUma(j);
          const id = `judge-${j.judge}`;
          const expanded = !!open[j.judge];
          return (
            <div
              key={j.judge}
              className={cn(
                "rounded-md border bg-card/40 p-2.5 transition-colors",
                hard ? "border-fuchsia-500/30" : "border-border/40",
                !passed && hard && "border-destructive/60 bg-destructive/5",
              )}
              aria-label={`${j.judge} score ${j.score > 0 ? j.score.toFixed(2) : "unscored"}`}
            >
              <button
                type="button"
                onClick={() => setOpen((prev) => ({ ...prev, [j.judge]: !prev[j.judge] }))}
                className="flex w-full items-start justify-between text-left focus:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                aria-expanded={expanded}
                aria-controls={id}
              >
                <span className="flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
                  {hard && (
                    <Star className="h-2.5 w-2.5 text-fuchsia-300" fill="currentColor" aria-label="Hard gate" />
                  )}
                  {j.judge}
                </span>
                <ChevronDown
                  className={cn("h-3 w-3 text-muted-foreground transition-transform", expanded && "rotate-180")}
                  aria-hidden
                />
              </button>
              <div className="mt-1 flex items-center justify-between">
                <span
                  className={cn(
                    "font-mono text-base",
                    j.score >= 0.9
                      ? "text-emerald-400"
                      : j.score >= 0.75
                        ? "text-primary"
                        : j.score > 0
                          ? "text-amber-400"
                          : "text-muted-foreground/50",
                  )}
                >
                  {j.score > 0 ? j.score.toFixed(2) : "—"}
                </span>
                <span
                  className={cn(
                    "font-mono text-[9px] uppercase tracking-wider",
                    passed ? "text-emerald-400" : "text-amber-400",
                  )}
                >
                  {passed ? "pass" : "fail"}
                </span>
              </div>
              {uma && (
                <div className="mt-1 inline-flex items-center gap-1 rounded border border-fuchsia-500/40 bg-fuchsia-500/10 px-1 py-0.5 font-mono text-[8px] uppercase tracking-wider text-fuchsia-300">
                  <Shield className="h-2.5 w-2.5" aria-hidden /> UMA dispute prevention
                </div>
              )}
              <AnimatePresence initial={false}>
                {expanded && (
                  <motion.div
                    id={id}
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.18, ease: "easeOut" }}
                    className="overflow-hidden"
                  >
                    <p className="mt-2 text-[10px] leading-relaxed text-foreground/80">
                      {j.notes ?? "No reasoning surfaced for this judge."}
                    </p>
                    {typeof j.weight === "number" && (
                      <p className="mt-1 font-mono text-[9px] text-muted-foreground">
                        weight · {j.weight.toFixed(2)} (closed-IP)
                      </p>
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </div>
    </div>
  );
}
