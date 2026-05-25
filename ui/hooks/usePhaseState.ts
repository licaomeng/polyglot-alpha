"use client";

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { PHASE_NAMES } from "@/lib/api";

/**
 * Shared state for DAG ↔ Timeline coupling. Both components subscribe via
 * {@link usePhaseState}; one side calling {@link setActivePhase} causes the
 * other to scroll + spotlight the matching node/card.
 */
interface PhaseStateValue {
  activePhase: number | null;
  /** 7-phase index (0-based) or null to clear. */
  setActivePhase: (idx: number | null) => void;
  /** Stable id used as a DOM anchor target. */
  phaseDomId: (idx: number) => string;
  /** Stable id used as the DAG node id. */
  phaseNodeId: (idx: number) => string;
}

const PhaseStateContext = createContext<PhaseStateValue | null>(null);

function buildPhaseDomId(idx: number): string {
  return `phase-card-${idx}`;
}

function buildPhaseNodeId(idx: number): string {
  return `phase-node-${idx}`;
}

export function PhaseStateProvider({ children }: { children: ReactNode }) {
  const [activePhase, setActivePhaseRaw] = useState<number | null>(null);

  const setActivePhase = useCallback((idx: number | null) => {
    if (idx === null) {
      setActivePhaseRaw(null);
      return;
    }
    if (idx < 0 || idx >= PHASE_NAMES.length) return;
    setActivePhaseRaw(idx);
    // Auto-clear after 2.5s so highlights are transient.
    setTimeout(() => {
      setActivePhaseRaw((current) => (current === idx ? null : current));
    }, 2500);
  }, []);

  const value = useMemo<PhaseStateValue>(
    () => ({
      activePhase,
      setActivePhase,
      phaseDomId: buildPhaseDomId,
      phaseNodeId: buildPhaseNodeId,
    }),
    [activePhase, setActivePhase],
  );

  return createElement(PhaseStateContext.Provider, { value }, children);
}

/**
 * Hook returning the shared phase-state. Returns a benign no-op shape when
 * called outside a provider so components remain rendererable in isolation.
 */
export function usePhaseState(): PhaseStateValue {
  const ctx = useContext(PhaseStateContext);
  if (ctx) return ctx;
  return {
    activePhase: null,
    setActivePhase: () => {},
    phaseDomId: buildPhaseDomId,
    phaseNodeId: buildPhaseNodeId,
  };
}

/** Scroll the timeline card for `idx` into view and trigger spotlight. */
export function scrollToPhaseCard(idx: number): void {
  if (typeof document === "undefined") return;
  const el = document.getElementById(buildPhaseDomId(idx));
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
}
