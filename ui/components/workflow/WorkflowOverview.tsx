"use client";

import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { PhaseNode } from "./PhaseNode";
import type { PhaseState } from "@/lib/api";
import { scrollToPhaseCard, usePhaseState } from "@/hooks/usePhaseState";

// 11 nodes laid out in a 4-row grid (≤ 4 per row) — fits a 720×600 canvas at
// fitView padding=0.25 so every node stays legible at default zoom.
//
// `phaseIndex` is the 0-based 7-phase index this graph-node maps to.
const NODE_DEFS: Array<{
  id: string;
  label: string;
  phase: string;
  phaseIndex: number;
  col: number;
  row: number;
}> = [
  { id: "ingest", label: "Event Ingest", phase: "Event Ingestion", phaseIndex: 0, col: 0, row: 0 },
  { id: "preproc", label: "Preprocess + NER", phase: "Event Ingestion", phaseIndex: 0, col: 1, row: 0 },
  { id: "auction", label: "USDC Auction", phase: "USDC Auction", phaseIndex: 1, col: 2, row: 0 },
  { id: "translate", label: "Translation L1–L5", phase: "Translation Pipeline", phaseIndex: 2, col: 3, row: 0 },
  { id: "debate", label: "Analyst Debate", phase: "Translation Pipeline", phaseIndex: 2, col: 3, row: 1 },
  { id: "synth", label: "Synthesizer", phase: "Translation Pipeline", phaseIndex: 2, col: 2, row: 1 },
  { id: "judges", label: "11-Judge Panel", phase: "11-Judge Panel", phaseIndex: 3, col: 1, row: 1 },
  { id: "anchor", label: "Arc Anchor", phase: "On-chain Anchor", phaseIndex: 4, col: 0, row: 1 },
  { id: "pmsubmit", label: "Polymarket Submit", phase: "Polymarket V2 Submission", phaseIndex: 5, col: 0, row: 2 },
  { id: "stream", label: "Revenue Stream", phase: "Streaming Revenue", phaseIndex: 6, col: 1, row: 2 },
  { id: "rep", label: "Reputation Update", phase: "Streaming Revenue", phaseIndex: 6, col: 2, row: 2 },
];

const COL_GAP = 240;
const ROW_GAP = 150;

const NODE_TYPES = { phase: PhaseNode };

export function WorkflowOverview({ phases }: { phases?: PhaseState[] }) {
  const { activePhase, setActivePhase } = usePhaseState();

  const statusByPhase = useMemo(() => {
    const map: Record<string, PhaseState["status"]> = {};
    phases?.forEach((p) => {
      map[p.name] = p.status;
    });
    return map;
  }, [phases]);

  const nodes: Node[] = useMemo(
    () =>
      NODE_DEFS.map((def, idx) => ({
        id: def.id,
        type: "phase",
        position: { x: def.col * COL_GAP, y: def.row * ROW_GAP },
        data: {
          label: def.label,
          index: idx + 1,
          status: statusByPhase[def.phase] ?? "pending",
          isActive: activePhase === def.phaseIndex,
          phaseIndex: def.phaseIndex,
        },
      })),
    [statusByPhase, activePhase],
  );

  const edges: Edge[] = useMemo(
    () =>
      NODE_DEFS.slice(0, -1).map((def, idx) => {
        const next = NODE_DEFS[idx + 1];
        const status = statusByPhase[next.phase];
        const isRunning = status === "running";
        // Pick handles based on grid geometry so the snake pattern routes
        // without crossings: same-row → horizontal; same-col → vertical;
        // otherwise default to right→left.
        let sourceHandle = "r-source";
        let targetHandle = "l-target";
        if (def.row === next.row) {
          if (def.col < next.col) {
            sourceHandle = "r-source"; targetHandle = "l-target";
          } else if (def.col > next.col) {
            sourceHandle = "l-source"; targetHandle = "r-target";
          }
        } else if (def.col === next.col) {
          if (def.row < next.row) {
            sourceHandle = "b-source"; targetHandle = "t-target";
          } else {
            sourceHandle = "t-source"; targetHandle = "b-target";
          }
        }
        return {
          id: `${def.id}->${next.id}`,
          source: def.id,
          target: next.id,
          sourceHandle,
          targetHandle,
          animated: isRunning,
          style: {
            stroke:
              status === "completed"
                ? "rgba(52,211,153,0.7)"
                : isRunning
                  ? "hsl(var(--primary))"
                  : "hsl(var(--border))",
            strokeWidth: isRunning ? 3 : 2.4,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 14,
            height: 14,
            color:
              status === "completed"
                ? "rgba(52,211,153,0.8)"
                : isRunning
                  ? "hsl(var(--primary))"
                  : "hsl(var(--border))",
          },
        };
      }),
    [statusByPhase],
  );

  const onNodeClick: NodeMouseHandler = useCallback(
    (_evt, node) => {
      const def = NODE_DEFS.find((d) => d.id === node.id);
      if (!def) return;
      setActivePhase(def.phaseIndex);
      scrollToPhaseCard(def.phaseIndex);
    },
    [setActivePhase],
  );

  return (
    <div className="relative h-[420px] w-full rounded-xl border border-border/60 bg-card/40 grid-bg sm:h-[520px] md:h-[600px]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodeClick={onNodeClick}
        fitView
        fitViewOptions={{ padding: 0.25, minZoom: 0.7, maxZoom: 1.2 }}
        minZoom={0.4}
        maxZoom={1.5}
        panOnDrag
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        panOnScroll={false}
        preventScrolling={false}
        edgesFocusable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} color="hsl(var(--border))" />
        <Controls
          showInteractive={false}
          className="!rounded-md !border !border-border/60 !bg-card/80"
        />
      </ReactFlow>
      <div className="absolute top-2 right-2 z-10 text-[9px] uppercase tracking-wider text-muted-foreground/60 pointer-events-none">
        drag to pan · use +/− buttons to zoom
      </div>
      <div className="absolute top-2 left-2 z-10 text-[9px] uppercase tracking-wider text-muted-foreground/60 pointer-events-none">
        11 graph nodes across 7 lifecycle phases
      </div>
    </div>
  );
}
