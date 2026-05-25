"use client";

import { Card, CardContent } from "@/components/ui/card";
import { PhaseHeader } from "@/components/shared/PhaseHeader";
import type { PhaseState } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { usePhaseState } from "@/hooks/usePhaseState";
import { useCallback } from "react";
import { cn } from "@/lib/utils";

export function PhaseCard({
  phase,
  index,
  children,
}: {
  phase: PhaseState;
  index: number;
  children?: React.ReactNode;
}) {
  // `index` is 0-based across the timeline. The shared phase-state maps it to
  // the matching DAG node so click events stay in sync both directions.
  const { activePhase, setActivePhase, phaseDomId } = usePhaseState();
  const isActive = activePhase === index;

  const onHeaderClick = useCallback(() => {
    setActivePhase(index);
  }, [index, setActivePhase]);

  return (
    <motion.div
      id={phaseDomId(index)}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.04, ease: "easeOut" }}
    >
      <Card
        className={cn(
          phase.status === "running"
            ? "border-primary/50 glow-cyan"
            : phase.status === "completed"
              ? "border-emerald-500/30"
              : phase.status === "failed"
                ? "border-destructive/40"
                : "border-border/60",
          isActive && "ring-2 ring-accent ring-offset-2 ring-offset-background",
        )}
      >
        <CardContent className="space-y-3 p-5">
          <button
            type="button"
            onClick={onHeaderClick}
            className="w-full cursor-pointer rounded text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`Spotlight phase ${phase.name} on the DAG overview`}
          >
            <PhaseHeader index={index + 1} title={phase.name} status={phase.status} />
          </button>
          <AnimatePresence initial={false}>
            {children && (
              <motion.div
                key="body"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.25, ease: "easeOut" }}
              >
                {children}
              </motion.div>
            )}
          </AnimatePresence>
        </CardContent>
      </Card>
    </motion.div>
  );
}
