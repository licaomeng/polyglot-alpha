"use client";

import { useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Bot, Check, Gavel, Info, Loader2, Sparkles, Wand2, X } from "lucide-react";
import type { EventDetail, PhaseState, PhaseStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

// ─── Types ─────────────────────────────────────────────────────────────────

/**
 * Shape the backend will eventually emit for each candidate question in the
 * L2→L3 debate round. Mirrored on the `phase.details.debate` payload until
 * the wiring agent lands a typed `EventDetail.debate` field.
 */
export interface DebateCandidate {
  /** Stable identifier (e.g. "A" / "B" or the bidder address). */
  id: string;
  /** The translator agent the candidate came from. */
  agent?: string;
  /** The candidate Polymarket-style question. */
  question: string;
  /** Critic feedback for this candidate (1 critic per candidate by default). */
  critic?: {
    issues: string[];
    strengths: string[];
    /** Critic LLM (e.g. "claude-sonnet"). */
    model?: string;
  };
}

export interface ModeratorVerdict {
  /** Candidate id the moderator picked. */
  pickedId: string;
  /** Free-form moderator reasoning (1–3 sentences). */
  reasoning: string;
  /** Critique signal forwarded to the L5 refine layer. */
  critiqueSignal?: string;
  /** Moderator LLM (e.g. "claude-sonnet"). */
  model?: string;
}

export interface RefineSummary {
  /** Final refined question after one critique-driven pass. */
  finalQuestion: string;
  /** Compact diff summary, e.g. "5 tokens added, 3 removed, edit dist 12". */
  diffSummary?: string;
  /** Pre-refine version (so the panel can show before/after). */
  originalQuestion?: string;
}

export interface AgentDebateState {
  candidates?: DebateCandidate[];
  moderator?: ModeratorVerdict;
  refine?: RefineSummary;
}

// ─── Demo data ─────────────────────────────────────────────────────────────

const DEMO_STATE: AgentDebateState = {
  candidates: [
    {
      id: "A",
      agent: "0xA1c9…f8d2",
      question:
        "Will the PBoC cut the 1-year LPR by at least 10 bps before 2026-07-31?",
      critic: {
        model: "claude-3-5-sonnet",
        issues: [
          "Ambiguous resolution source — does not specify whether the PBoC press release or the NIFC fixing rate is canonical.",
          "10 bps threshold may be too coarse; recent cuts have been 5 bps.",
        ],
        strengths: [
          "Clear deadline anchored to a fixing date.",
          "Translated headline numerals match the original Mandarin source.",
        ],
      },
    },
    {
      id: "B",
      agent: "0xB7d4…1c0e",
      question:
        "Will China's central bank announce any policy-rate easing measure by July 31, 2026?",
      critic: {
        model: "claude-3-5-sonnet",
        issues: [
          "‘Any easing measure’ is too broad — RRR cuts and MLF tweaks both qualify, hurting tradeability.",
          "No specific numerical threshold for resolution.",
        ],
        strengths: [
          "Captures the broader policy stance from the source article.",
          "Date format unambiguous.",
        ],
      },
    },
  ],
  moderator: {
    pickedId: "A",
    model: "claude-3-5-sonnet",
    reasoning:
      "Candidate A trades cleaner: explicit instrument (1Y LPR), explicit magnitude (≥10 bps), explicit deadline. The critic flags about the threshold are valid but addressable in refine; B's ambiguity is structural and would hurt builder-fee yield.",
    critiqueSignal:
      "Lower the 10 bps threshold to 5 bps and pin the resolution source to the PBoC monthly fixing release.",
  },
  refine: {
    originalQuestion:
      "Will the PBoC cut the 1-year LPR by at least 10 bps before 2026-07-31?",
    finalQuestion:
      "Will the PBoC cut the 1-year LPR by at least 5 bps in its monthly fixing release before 2026-07-31?",
    diffSummary: "10 bps → 5 bps · added ‘monthly fixing release’ · edit dist 18",
  },
};

// ─── Helpers ───────────────────────────────────────────────────────────────

/** Extract the debate state from the event payload. Returns null if absent. */
function extractDebateState(event: EventDetail): AgentDebateState | null {
  // Read from phase 2 (Translation Pipeline) details, where the backend will
  // attach the debate payload as `details.debate`. We don't have a typed
  // backend contract yet, so we lean on a structural read.
  const phases: PhaseState[] = event.phases ?? [];
  const phase2 = phases[2];
  if (!phase2) return null;
  const details = phase2.details as
    | {
        debate?: AgentDebateState;
        candidates?: DebateCandidate[];
        moderator?: ModeratorVerdict;
        refine?: RefineSummary;
      }
    | undefined;
  if (!details) return null;
  if (details.debate) return details.debate;
  // Fallback: backend may attach the three sub-records flat on the phase
  // details object instead of under a `debate` key.
  if (details.candidates || details.moderator || details.refine) {
    return {
      candidates: details.candidates,
      moderator: details.moderator,
      refine: details.refine,
    };
  }
  return null;
}

/** Compute which debate stage the lifecycle has reached. */
function debateStage(event: EventDetail): "pre-l3" | "l3" | "l4" | "l5" | "done" {
  const phase2 = (event.phases ?? [])[2];
  const subPhases =
    (phase2?.details?.subPhases as Record<string, string> | undefined) ?? {};
  if (subPhases["L5 Refine"] === "completed") return "done";
  if (subPhases["L5 Refine"] === "running") return "l5";
  if (subPhases["L4 Moderator"] === "running") return "l4";
  if (subPhases["L3 Critics"] === "completed") return "l4";
  if (subPhases["L3 Critics"] === "running") return "l3";
  return "pre-l3";
}

// ─── Debate steps (5-chip progression) ────────────────────────────────────

const DEBATE_STEPS = [
  { key: "proposing", label: "Proposing", description: "L1/L2 translators generate candidate questions." },
  { key: "critiquing", label: "Critiquing", description: "L3 critics list strengths + issues per candidate." },
  { key: "moderating", label: "Moderating", description: "L4 moderator picks the best candidate." },
  { key: "refining", label: "Refining", description: "L5 refine pass applies the critique signal." },
  { key: "finalized", label: "Finalized", description: "Refined question persisted as final_question." },
] as const;

type DebateStepKey = typeof DEBATE_STEPS[number]["key"];

function debateStepStatus(
  event: EventDetail,
): Record<DebateStepKey, PhaseStatus> {
  const phase2 = (event.phases ?? [])[2];
  const sub =
    (phase2?.details?.subPhases as Record<string, PhaseStatus> | undefined) ?? {};
  const map: Record<DebateStepKey, PhaseStatus> = {
    proposing: "pending",
    critiquing: "pending",
    moderating: "pending",
    refining: "pending",
    finalized: "pending",
  };

  // L2 translators done ⇒ "proposing" done.
  const l2 = sub["L2 Translators"];
  if (l2 === "completed" || sub["L3 Critics"] || sub["L4 Moderator"] || sub["L5 Refine"]) {
    map.proposing = "completed";
  } else if (phase2?.status === "running") {
    map.proposing = "running";
  } else if (phase2?.status === "completed") {
    map.proposing = "completed";
  }

  // L3 critics
  const l3 = sub["L3 Critics"];
  if (l3 === "completed") map.critiquing = "completed";
  else if (l3 === "running") map.critiquing = "running";
  else if (l3 === "failed") map.critiquing = "failed";
  else if (map.proposing === "completed" && phase2?.status === "running") {
    map.critiquing = "running";
  }

  // L4 moderator
  const l4 = sub["L4 Moderator"];
  if (l4 === "completed") map.moderating = "completed";
  else if (l4 === "running") map.moderating = "running";
  else if (l4 === "failed") map.moderating = "failed";

  // L5 refine
  const l5 = sub["L5 Refine"];
  if (l5 === "completed") map.refining = "completed";
  else if (l5 === "running") map.refining = "running";
  else if (l5 === "failed") map.refining = "failed";

  // Finalized when phase 2 is completed.
  if (phase2?.status === "completed") {
    map.finalized = "completed";
    map.refining = map.refining === "pending" ? "completed" : map.refining;
    map.moderating = map.moderating === "pending" ? "completed" : map.moderating;
    map.critiquing = map.critiquing === "pending" ? "completed" : map.critiquing;
  }
  if (phase2?.status === "failed") {
    // Whichever step was last running is marked failed; others stay.
    if (map.refining === "running") map.refining = "failed";
    else if (map.moderating === "running") map.moderating = "failed";
    else if (map.critiquing === "running") map.critiquing = "failed";
    else if (map.proposing === "running") map.proposing = "failed";
  }
  return map;
}

function DebateStepStrip({ event }: { event: EventDetail }) {
  const statuses = debateStepStatus(event);
  return (
    <ol
      className="flex flex-wrap items-center gap-1.5"
      aria-label="Debate process steps"
      data-testid="debate-step-strip"
    >
      {DEBATE_STEPS.map((step, idx) => {
        const status = statuses[step.key];
        const isLast = idx === DEBATE_STEPS.length - 1;
        const tone =
          status === "completed"
            ? "border-emerald-500/40 bg-emerald-500/[0.08] text-emerald-300"
            : status === "running"
              ? "border-primary/50 bg-primary/[0.08] text-primary"
              : status === "failed"
                ? "border-destructive/50 bg-destructive/[0.08] text-destructive"
                : "border-border/60 bg-muted/20 text-muted-foreground";
        return (
          <li key={step.key} className="flex items-center gap-1.5">
            <span
              title={step.description}
              aria-label={`${step.label} (${status})`}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider",
                tone,
              )}
            >
              {status === "completed" && (
                <Check className="h-3 w-3" aria-hidden />
              )}
              {status === "running" && (
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              )}
              {status === "failed" && <X className="h-3 w-3" aria-hidden />}
              {status === "pending" && (
                <span
                  className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60"
                  aria-hidden
                />
              )}
              {step.label}
            </span>
            {!isLast && (
              <span
                className={cn(
                  "h-px w-3 sm:w-5",
                  status === "completed"
                    ? "bg-emerald-500/40"
                    : "bg-border/60",
                )}
                aria-hidden
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

// ─── Sub-components ────────────────────────────────────────────────────────

function CandidateCard({
  candidate,
  isWinner,
}: {
  candidate: DebateCandidate;
  isWinner: boolean;
}) {
  return (
    <Card
      className={
        isWinner
          ? "border-emerald-500/40 bg-emerald-500/[0.03]"
          : "border-border/60"
      }
    >
      <CardHeader className="space-y-1 pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-semibold">
            Candidate {candidate.id}
          </CardTitle>
          {isWinner && (
            <Badge className="bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/15">
              moderator pick
            </Badge>
          )}
        </div>
        {candidate.agent && (
          <p className="font-mono text-[10px] text-muted-foreground">
            {candidate.agent}
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        <blockquote className="rounded-md border border-border/40 bg-muted/20 p-3 text-foreground/90 leading-relaxed">
          “{candidate.question}”
        </blockquote>
        {candidate.critic ? (
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <Bot className="h-3 w-3" aria-hidden />
              critic
              {candidate.critic.model && (
                <span className="text-muted-foreground/60">
                  · {candidate.critic.model}
                </span>
              )}
            </div>
            {candidate.critic.strengths.length > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-emerald-300/80">
                  Strengths
                </p>
                <ul className="ml-3 list-disc space-y-0.5 text-foreground/80 marker:text-emerald-400/60">
                  {candidate.critic.strengths.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            )}
            {candidate.critic.issues.length > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-amber-300/80">
                  Issues
                </p>
                <ul className="ml-3 list-disc space-y-0.5 text-foreground/80 marker:text-amber-400/60">
                  {candidate.critic.issues.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : (
          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            awaiting critic…
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function ModeratorCard({
  moderator,
  candidates,
}: {
  moderator: ModeratorVerdict | undefined;
  candidates: DebateCandidate[] | undefined;
}) {
  const winner = candidates?.find((c) => c.id === moderator?.pickedId);
  return (
    <Card className="border-primary/30 bg-primary/[0.04]">
      <CardHeader className="space-y-1 pb-3">
        <div className="flex items-center gap-2">
          <Gavel className="h-4 w-4 text-primary" aria-hidden />
          <CardTitle className="text-sm font-semibold">L4 · Moderator</CardTitle>
          {moderator?.model && (
            <span className="font-mono text-[10px] text-muted-foreground">
              {moderator.model}
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        {moderator ? (
          <>
            <p className="text-foreground/90">
              Picked{" "}
              <span className="font-semibold text-emerald-300">
                Candidate {moderator.pickedId}
              </span>
              {winner?.agent && (
                <span className="font-mono text-muted-foreground">
                  {" "}
                  · {winner.agent}
                </span>
              )}
              .
            </p>
            <p className="text-foreground/80 leading-relaxed">
              {moderator.reasoning}
            </p>
            {moderator.critiqueSignal && (
              <div className="rounded-md border border-amber-500/30 bg-amber-500/[0.04] p-2">
                <p className="text-[10px] uppercase tracking-wider text-amber-300/80">
                  Critique signal → L5
                </p>
                <p className="text-foreground/85">{moderator.critiqueSignal}</p>
              </div>
            )}
          </>
        ) : (
          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            awaiting moderator verdict…
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function RefineCard({ refine }: { refine: RefineSummary | undefined }) {
  return (
    <Card className="border-emerald-500/30 bg-emerald-500/[0.03]">
      <CardHeader className="space-y-1 pb-3">
        <div className="flex items-center gap-2">
          <Wand2 className="h-4 w-4 text-emerald-300" aria-hidden />
          <CardTitle className="text-sm font-semibold">L5 · Refine</CardTitle>
          {refine?.diffSummary && (
            <span className="font-mono text-[10px] text-muted-foreground">
              {refine.diffSummary}
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        {refine ? (
          <>
            {refine.originalQuestion && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  before
                </p>
                <blockquote className="rounded-md border border-border/40 bg-muted/20 p-2 text-foreground/70 line-through decoration-amber-400/50">
                  {refine.originalQuestion}
                </blockquote>
              </div>
            )}
            <div>
              <p className="text-[10px] uppercase tracking-wider text-emerald-300/80">
                after
              </p>
              <blockquote className="rounded-md border border-emerald-500/30 bg-emerald-500/[0.05] p-2 text-foreground/95 leading-relaxed">
                {refine.finalQuestion}
              </blockquote>
            </div>
          </>
        ) : (
          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            awaiting refine pass…
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Main panel ────────────────────────────────────────────────────────────

export function AgentDebatePanel({ event }: { event: EventDetail }) {
  const [demoMode, setDemoMode] = useState(false);

  const liveState = useMemo(() => extractDebateState(event), [event]);
  const stage = useMemo(() => debateStage(event), [event]);

  const state: AgentDebateState | null = demoMode
    ? DEMO_STATE
    : liveState ?? null;

  // Hide gracefully until the lifecycle has reached at least L3 — UNLESS the
  // user has flipped on demo mode, which always renders the full panel.
  const hasReachedL3 = stage !== "pre-l3";
  if (!demoMode && !state && !hasReachedL3) {
    return (
      <section
        aria-label="Agent debate"
        data-testid="agent-debate-panel-empty"
        className="rounded-xl border border-dashed border-border/50 bg-muted/10 p-5"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5" aria-hidden />
            Agent debate (L3 Critics → L4 Moderator → L5 Refine) will appear
            here once the translation pipeline advances past L2.
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setDemoMode(true)}
            className="text-[10px]"
          >
            Show demo data
          </Button>
        </div>
      </section>
    );
  }

  const candidates = state?.candidates ?? [];
  const moderator = state?.moderator;
  const refine = state?.refine;

  return (
    <section
      aria-label="Agent debate"
      data-testid="agent-debate-panel"
      className="space-y-4"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold">
              Reference Seeder · Internal Debate Loop
            </h2>
            <span
              className="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-border/60 text-muted-foreground hover:text-foreground"
              title="The marketplace doesn't impose a method, only verifies (bid + candidate_hash + stake). Reference seeders use this critic-moderator-refine pipeline as one viable approach."
              aria-label="Method-agnostic verification info"
            >
              <Info className="h-3 w-3" aria-hidden />
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            This is one of our 3 reference seeder agents&apos; internal method.
            External operators are free to use any approach — single-shot,
            multi-agent debate, RAG, anything. Agent debate (L3 Critics → L4
            Moderator → L5 Refine).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge className="bg-muted text-foreground/80 hover:bg-muted">
            stage · {stage.toUpperCase()}
          </Badge>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setDemoMode((v) => !v)}
            className="text-[10px]"
            aria-pressed={demoMode}
          >
            {demoMode ? "Hide demo data" : "Show demo data"}
          </Button>
        </div>
      </header>

      <Separator />

      <DebateStepStrip event={event} />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {candidates.length > 0 ? (
          candidates.map((c) => (
            <CandidateCard
              key={c.id}
              candidate={c}
              isWinner={moderator?.pickedId === c.id}
            />
          ))
        ) : (
          <>
            <CandidateCard
              candidate={{
                id: "A",
                question: "(awaiting L2 translator candidate A…)",
              }}
              isWinner={false}
            />
            <CandidateCard
              candidate={{
                id: "B",
                question: "(awaiting L2 translator candidate B…)",
              }}
              isWinner={false}
            />
          </>
        )}
      </div>

      <ModeratorCard moderator={moderator} candidates={candidates} />
      <RefineCard refine={refine} />
    </section>
  );
}
