"use client";

import { useState, useCallback, type ReactNode } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { IngestionSourcesView } from "./IngestionSourcesView";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn, shortAddr, formatUsd } from "@/lib/utils";
import { arcTxUrl, type EventDetail, type PhaseState } from "@/lib/api";

interface PhaseDetailsAccordionProps {
  phase: PhaseState;
  index: number;
  event: EventDetail;
}

/**
 * Progressive-disclosure container that renders a "What did this phase
 * actually do?" panel beneath every PhaseCard. Each phase index selects a
 * dedicated detail sub-component; the accordion is collapsed by default so
 * the timeline stays scannable, and expanded by the user when they want
 * the full transparency view.
 */
export function PhaseDetailsAccordion({
  phase,
  index,
  event,
}: PhaseDetailsAccordionProps) {
  const [open, setOpen] = useState(false);
  const toggle = useCallback(
    (e: React.MouseEvent<HTMLButtonElement>) => {
      // The accordion lives inside the PhaseCard's clickable header region.
      // Stop propagation so toggling details doesn't also re-spotlight the
      // phase on the DAG (and navigate elsewhere).
      e.stopPropagation();
      setOpen((v) => !v);
    },
    [],
  );

  const body = renderPhaseDetails(phase, index, event);
  if (!body) return null;

  const labelId = `phase-${index}-details-label`;
  const regionId = `phase-${index}-details-region`;

  return (
    <div
      className="mt-3 rounded-md border border-border/40 bg-muted/[0.04]"
      data-testid={`phase-details-${index}`}
    >
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls={regionId}
        id={labelId}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-foreground/80 transition-colors hover:bg-muted/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
        )}
        <span className="font-mono text-[10px] uppercase tracking-wider">
          inputs · outputs · diagram
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="details-body"
            id={regionId}
            role="region"
            aria-labelledby={labelId}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className="border-t border-border/40 p-3">{body}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ─── Per-phase detail sub-components ──────────────────────────────────────

function renderPhaseDetails(
  phase: PhaseState,
  index: number,
  event: EventDetail,
): ReactNode {
  switch (phase.name) {
    case "Event Ingestion":
      return <IngestionSourcesView event={event} />;
    case "USDC Auction":
      return <AuctionDetails event={event} phase={phase} />;
    case "Translation Pipeline":
      return <PipelineDetails event={event} />;
    case "11-Judge Panel":
      return <JudgeDetails event={event} />;
    case "On-chain Anchor":
      return <AnchorDetails event={event} phase={phase} />;
    case "Polymarket V2 Submission":
      return <PolymarketDetails event={event} phase={phase} />;
    case "Streaming Revenue":
      return <RevenueDetails event={event} />;
    default:
      // Generic placeholder so future phases still expand without crashing.
      return (
        <p className="font-mono text-[10px] text-muted-foreground">
          phase index {index} · no extra details available yet
        </p>
      );
  }
}

// ─── Shared primitives ─────────────────────────────────────────────────────

const IO_COLS = "grid grid-cols-1 gap-3 sm:grid-cols-2";

function IOSection({
  inputs,
  outputs,
}: {
  inputs: { label: string; value: ReactNode }[];
  outputs: { label: string; value: ReactNode }[];
}) {
  return (
    <div className={IO_COLS}>
      <Card className="border-border/60">
        <CardContent className="space-y-2 p-3">
          <p className="font-mono text-[9px] uppercase tracking-wider text-cyan-300/80">
            input
          </p>
          <ul className="space-y-1 text-xs">
            {inputs.map((row) => (
              <li key={row.label} className="flex flex-col gap-0.5">
                <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                  {row.label}
                </span>
                <span className="text-foreground/85">{row.value}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
      <Card className="border-border/60">
        <CardContent className="space-y-2 p-3">
          <p className="font-mono text-[9px] uppercase tracking-wider text-emerald-300/80">
            output
          </p>
          <ul className="space-y-1 text-xs">
            {outputs.map((row) => (
              <li key={row.label} className="flex flex-col gap-0.5">
                <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                  {row.label}
                </span>
                <span className="text-foreground/85">{row.value}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function DiagramRow({ steps }: { steps: string[] }) {
  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded-md border border-border/40 bg-muted/[0.04] p-2 font-mono text-[10px] text-foreground/80"
      aria-label="Phase pipeline diagram"
    >
      {steps.map((step, idx) => (
        <span key={`${step}-${idx}`} className="inline-flex items-center gap-2">
          <span className="rounded-md border border-primary/40 bg-primary/[0.06] px-2 py-1 text-primary">
            {step}
          </span>
          {idx < steps.length - 1 && (
            <span aria-hidden className="text-muted-foreground">
              →
            </span>
          )}
        </span>
      ))}
    </div>
  );
}

interface PhaseDetails {
  tx_hash?: string;
  market_id?: string;
  market_url?: string;
  is_simulated?: boolean;
  question_id?: string;
  builder_code?: string;
  winner_address?: string;
  winning_bid?: number;
  // Auction diagnostics (populated by the backend when one or more
  // seeder wallets were skipped pre-flight due to low gas).
  reason?: string;
  partial_auction?: boolean;
  skipped_bidders?: string[];
  skip_reasons?: Record<string, string>;
  balances_eth?: Record<string, number>;
  threshold_eth?: number;
}

function detailsAt(phases: PhaseState[] | undefined, idx: number): PhaseDetails {
  const p = phases?.[idx];
  return (p?.details ?? {}) as PhaseDetails;
}

// ─── Auction details ──────────────────────────────────────────────────────

function AuctionDetails({
  event,
  phase,
}: {
  event: EventDetail;
  phase: PhaseState;
}) {
  const idx = (event.phases ?? []).indexOf(phase);
  const det = detailsAt(event.phases, idx >= 0 ? idx : 1);
  const winningBid = event.bids?.find((b) => b.winner);
  const isAllLowGas =
    phase.status === "failed" && det.reason === "all_seeders_low_gas";
  const skippedNames = det.skipped_bidders ?? [];
  const skipReasons = det.skip_reasons ?? {};
  const balances = det.balances_eth ?? {};
  const thresholdEth = det.threshold_eth;
  const isPartial = Boolean(det.partial_auction) && skippedNames.length > 0;
  const formatEth = (eth?: number) =>
    typeof eth === "number" && Number.isFinite(eth)
      ? `${eth.toFixed(4)} ETH`
      : "—";
  return (
    <div className="space-y-3">
      {isAllLowGas && (
        <div
          role="alert"
          data-testid="auction-low-gas-panel"
          className="rounded-md border border-amber-500/40 bg-amber-500/[0.06] p-3 text-amber-200/90"
        >
          <p className="font-mono text-[11px] uppercase tracking-wider text-amber-300">
            All 3 reference seeders out of gas
          </p>
          <ul className="mt-2 space-y-1 text-xs">
            {skippedNames.map((name) => {
              const reason = skipReasons[name] ?? "low_gas";
              const eth = balances[name];
              return (
                <li
                  key={name}
                  className="flex items-center justify-between gap-3 font-mono"
                >
                  <span>
                    {name}
                    {reason !== "low_gas" ? ` (${reason})` : ""}
                  </span>
                  <span className="text-amber-100/80">
                    {formatEth(eth)}
                    {typeof thresholdEth === "number"
                      ? ` (needs ${thresholdEth.toFixed(4)})`
                      : ""}
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="mt-2 text-[11px] text-amber-200/70">
            Refund seeder wallets to restore the demo.
          </p>
        </div>
      )}
      {!isAllLowGas && isPartial && (
        <div
          data-testid="auction-partial-note"
          className="rounded-md border border-amber-500/30 bg-amber-500/[0.04] p-2 text-xs text-amber-200/80"
        >
          <p className="font-mono text-[10px] uppercase tracking-wider text-amber-300/90">
            Partial auction: {skippedNames.length}/3 seeders skipped (low gas)
          </p>
          <ul className="mt-1 space-y-0.5">
            {skippedNames.map((name) => (
              <li
                key={name}
                className="flex items-center justify-between font-mono"
              >
                <span>{name}</span>
                <span className="text-amber-100/70">
                  {formatEth(balances[name])}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <IOSection
        inputs={[
          { label: "event_id", value: <span className="font-mono">{event.id}</span> },
          {
            label: "content_hash",
            value: <span className="font-mono">32-byte SHA256(headline + body)</span>,
          },
          { label: "auction window", value: "60s sealed-bid" },
          { label: "reputation gate", value: "≥ 0.70 to bid" },
        ]}
        outputs={[
          {
            label: "winner_address",
            value: winningBid?.agent ? (
              <span className="font-mono">{winningBid.agent.slice(0, 10)}…</span>
            ) : (
              "—"
            ),
          },
          {
            label: "winning_bid",
            value: winningBid?.bid !== undefined ? `$${winningBid.bid.toFixed(2)}` : "—",
          },
          {
            label: "settle_tx_hash",
            value: det.tx_hash ? (
              <a
                href={`https://explorer.arc-testnet.io/tx/${det.tx_hash}`}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 font-mono text-primary hover:underline"
              >
                {det.tx_hash.slice(0, 14)}…
                <ExternalLink className="h-3 w-3" aria-hidden />
              </a>
            ) : (
              "—"
            ),
          },
        ]}
      />
      <DiagramRow
        steps={[
          "bidders",
          "submitBid (parallel)",
          "settleAuction",
          "highest rep-adj score wins",
        ]}
      />
    </div>
  );
}

// ─── Pipeline details ─────────────────────────────────────────────────────

const PIPELINE_STEPS: {
  layer: string;
  what: string;
  input: string;
  output: string;
  cost: string;
}[] = [
  {
    layer: "L1 · Analysts",
    what: "3 parallel analysts frame the event from distinct angles",
    input: "event headline + body + cluster context",
    output: "3 framings (markets / politics / world)",
    cost: "~$0.001",
  },
  {
    layer: "L2 · Translators",
    what: "Translate each framing into Polymarket-style English",
    input: "3 framings",
    output: "3 candidate questions",
    cost: "~$0.001",
  },
  {
    layer: "L3 · Critics",
    what: "3 critics flag structural / stylistic / framing issues",
    input: "3 candidates",
    output: "3 critique reports",
    cost: "~$0.001",
  },
  {
    layer: "L4 · Moderator",
    what: "Synthesizes the critique reports into a single verdict",
    input: "3 critiques + 3 candidates",
    output: "moderator verdict + best candidate",
    cost: "~$0.001",
  },
  {
    layer: "L5 · Refine",
    what: "Final pass: tighten wording, set resolution source + cutoff",
    input: "best candidate + verdict",
    output: "final_question (sent to judge panel)",
    cost: "~$0.001",
  },
];

function PipelineDetails({ event }: { event: EventDetail }) {
  return (
    <div className="space-y-3">
      <ul className="space-y-2" aria-label="Translation pipeline steps">
        {PIPELINE_STEPS.map((step) => (
          <li key={step.layer}>
            <Card className="border-border/60">
              <CardContent className="space-y-1.5 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-[11px] font-semibold text-foreground/90">
                    {step.layer}
                  </span>
                  <Badge className="font-mono text-[9px] uppercase tracking-wider">
                    {step.cost}
                  </Badge>
                </div>
                <p className="text-xs text-foreground/85">{step.what}</p>
                <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                  <p className="font-mono text-[10px] text-muted-foreground">
                    <span className="text-cyan-300/80">in · </span>
                    {step.input}
                  </p>
                  <p className="font-mono text-[10px] text-muted-foreground">
                    <span className="text-emerald-300/80">out · </span>
                    {step.output}
                  </p>
                </div>
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
      <p className="font-mono text-[10px] text-muted-foreground">
        winner · {event.translation ? "ran" : "pending"} · synthesizer = L4 moderator
      </p>
    </div>
  );
}

// ─── Judge details ────────────────────────────────────────────────────────

interface DossierJudge {
  name: string;
  passed: boolean;
  score: number;
  reason: string;
  panelBudgetExceeded?: boolean;
  softSkip?: boolean;
  timeout?: boolean;
  panelPartial?: boolean;
}

function isDossierJudge(x: unknown): x is DossierJudge {
  if (typeof x !== "object" || x === null) return false;
  const j = x as Record<string, unknown>;
  return typeof j.name === "string" && typeof j.passed === "boolean";
}

function JudgeDetails({ event }: { event: EventDetail }) {
  const verdict = event.overallVerdict ?? event.verdict;
  const reasoning = event.overallReasoning;
  // ``event.judges`` may be the new dossier shape from the backend OR the
  // legacy ``JudgeScore[]`` shape; only the dossier carries
  // ``panelBudgetExceeded`` so we filter for that.
  const rawJudges = (event.judges ?? []) as unknown[];
  const dossier: DossierJudge[] = rawJudges.filter(isDossierJudge);
  const panelPartial =
    Boolean(event.panelPartial) || dossier.some((j) => j.panelBudgetExceeded);
  const partialCount = dossier.filter((j) => j.panelBudgetExceeded).length;
  const completedCount = dossier.length - partialCount;

  return (
    <div className="space-y-3">
      {/* Panel-wide partial header */}
      {panelPartial && dossier.length > 0 && (
        <div
          className="rounded-md border border-amber-500/40 bg-amber-500/[0.06] p-3 text-xs"
          data-testid="judge-panel-partial-header"
        >
          <p className="font-mono text-[10px] uppercase tracking-wider text-amber-300">
            partial · {completedCount}/{dossier.length} judges returned
          </p>
          <p className="mt-1 text-foreground/85">
            {partialCount} judge(s) exceeded the panel budget and returned
            <span className="font-mono"> INSUFFICIENT_DATA</span>. The verdict
            was aggregated from the completed judges; pending judges are
            highlighted below.
          </p>
          {Array.isArray(event.pendingJudgeNames) &&
            event.pendingJudgeNames.length > 0 && (
              <p className="mt-1 font-mono text-[10px] text-amber-300/80">
                pending · {event.pendingJudgeNames.join(", ")}
              </p>
            )}
        </div>
      )}

      <div className="space-y-2">
        <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          aggregation logic
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Card className="border-amber-500/30 bg-amber-500/[0.04]">
            <CardContent className="space-y-1 p-3">
              <p className="font-mono text-[10px] uppercase tracking-wider text-amber-300">
                HARD gates · all must pass
              </p>
              <ul className="space-y-0.5 text-xs text-foreground/85">
                <li>D1 · Structural</li>
                <li>D5 · Resolution Clarity</li>
                <li>D8 · Duplicate Detection</li>
                <li>MQM ≥ 80</li>
              </ul>
            </CardContent>
          </Card>
          <Card className="border-cyan-500/30 bg-cyan-500/[0.04]">
            <CardContent className="space-y-1 p-3">
              <p className="font-mono text-[10px] uppercase tracking-wider text-cyan-300">
                SOFT gates · ≥4 of 5
              </p>
              <ul className="space-y-0.5 text-xs text-foreground/85">
                <li>D2 · Stylistic</li>
                <li>D3 · Framing</li>
                <li>D4 · Granularity</li>
                <li>D6 · Source Reliability</li>
                <li>D7 · Leading</li>
              </ul>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Per-judge dossier (name + score + pass/fail + reason). */}
      {dossier.length > 0 && (
        <div className="space-y-1.5" data-testid="judge-panel-dossier">
          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            per-judge verdicts · {dossier.length} total
          </p>
          <ul className="divide-y divide-border/40 rounded-md border border-border/40 bg-card/40">
            {dossier.map((j) => {
              const isPartial = Boolean(j.panelBudgetExceeded);
              const isSoftSkip = Boolean(j.softSkip);
              return (
                <li
                  key={j.name}
                  className="flex flex-wrap items-start justify-between gap-2 px-3 py-2 text-xs"
                >
                  <div className="flex min-w-[120px] flex-col">
                    <span className="font-mono text-[11px] font-semibold text-foreground/90">
                      {j.name}
                    </span>
                    <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                      score · {j.score.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex flex-1 flex-col gap-1">
                    <p
                      className="text-foreground/80"
                      data-testid={`judge-row-${j.name}-reason`}
                    >
                      {j.reason || "—"}
                    </p>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {isPartial ? (
                        <Badge
                          className="border-amber-500/40 bg-amber-500/10 font-mono text-[9px] uppercase tracking-wider text-amber-300"
                          data-testid={`judge-row-${j.name}-partial`}
                        >
                          partial · INSUFFICIENT_DATA
                        </Badge>
                      ) : j.passed ? (
                        <Badge className="border-emerald-500/40 bg-emerald-500/10 font-mono text-[9px] uppercase tracking-wider text-emerald-300">
                          pass
                        </Badge>
                      ) : (
                        <Badge className="border-destructive/40 bg-destructive/10 font-mono text-[9px] uppercase tracking-wider text-destructive">
                          fail
                        </Badge>
                      )}
                      {isSoftSkip && (
                        <Badge className="border-cyan-500/40 bg-cyan-500/10 font-mono text-[9px] uppercase tracking-wider text-cyan-300">
                          soft-skip
                        </Badge>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {verdict && (
        <div
          className={cn(
            "rounded-md border p-3 text-xs",
            verdict === "FAIL" || verdict === "REJECTED"
              ? "border-destructive/40 bg-destructive/[0.04] text-destructive"
              : "border-emerald-500/40 bg-emerald-500/[0.04] text-emerald-300",
          )}
        >
          <p className="font-mono text-[10px] uppercase tracking-wider">
            why this verdict fired
          </p>
          <p className="mt-1 text-foreground/90">
            Verdict <span className="font-mono">{verdict}</span>
            {reasoning ? ` — ${reasoning}` : ""}
          </p>
        </div>
      )}
    </div>
  );
}

// ─── Anchor details ───────────────────────────────────────────────────────

interface AnchorPhaseDetails extends PhaseDetails {
  reasoning_ipfs?: string;
}

function AnchorDetails({
  event,
  phase,
}: {
  event: EventDetail;
  phase: PhaseState;
}) {
  const idx = (event.phases ?? []).indexOf(phase);
  const det = detailsAt(event.phases, idx >= 0 ? idx : 4) as AnchorPhaseDetails;
  const ipfs = event.anchor?.ipfsCid ?? det.reasoning_ipfs;
  return (
    <div className="space-y-3">
      <IOSection
        inputs={[
          { label: "candidate_hash", value: <span className="font-mono">SHA256(final_question)</span> },
          {
            label: "ipfs_cid",
            value: ipfs ? (
              <span className="font-mono">{ipfs.slice(0, 18)}…</span>
            ) : (
              "—"
            ),
          },
          {
            label: "builder_code",
            value: <span className="font-mono">{event.polymarket?.builderCode ?? "—"}</span>,
          },
        ]}
        outputs={[
          {
            label: "question_id",
            value: det.question_id ? (
              <span className="font-mono">{det.question_id}</span>
            ) : (
              "—"
            ),
          },
          {
            label: "commit_tx_hash",
            value: det.tx_hash ?? event.anchor?.txHash ? (
              <a
                href={
                  event.anchor?.explorerUrl ??
                  `https://explorer.arc-testnet.io/tx/${det.tx_hash}`
                }
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 font-mono text-primary hover:underline"
              >
                {(det.tx_hash ?? event.anchor?.txHash ?? "").slice(0, 14)}…
                <ExternalLink className="h-3 w-3" aria-hidden />
              </a>
            ) : (
              "—"
            ),
          },
          {
            label: "contract",
            value: event.anchor?.contractAddress ? (
              <span className="font-mono">
                QuestionRegistry · {event.anchor.contractAddress.slice(0, 10)}…
              </span>
            ) : (
              <span className="font-mono">QuestionRegistry</span>
            ),
          },
        ]}
      />
    </div>
  );
}

// ─── Polymarket details ───────────────────────────────────────────────────

// Color tokens for each known submission mode badge.
const MODE_BADGE_CLASSES: Record<string, string> = {
  real: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  live: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  dry_run: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  simulated: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  mock: "border-rose-500/40 bg-rose-500/10 text-rose-300",
};

const DASH = "—";

function formatEndDateUtc(iso?: string | null): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z")} UTC`;
}

interface PolymarketPayload {
  question?: string;
  outcomes?: string[];
  resolution_source?: string;
  end_date_iso?: string;
  initial_liquidity_usdc?: number;
  external_id?: string;
  builder_name?: string;
  builder_code?: string;
  category?: string;
  client_id?: string;
}

function PolymarketDetails({
  event,
  phase,
}: {
  event: EventDetail;
  phase: PhaseState;
}) {
  // When the event was rejected or hard-failed by the quality panel, no
  // Polymarket submission was attempted — surface that explicitly instead of
  // falling through to a card that contradicts itself (e.g. "MODE: real"
  // alongside a "MOCK" badge because every input dereferences to a fallback).
  const status = String(event.status ?? "").toUpperCase();
  const submissionSkipped =
    event.polymarket == null && (status === "REJECTED" || status === "FAILED");
  if (submissionSkipped) {
    const reason =
      status === "REJECTED"
        ? "REJECTED by the 11-judge panel"
        : "FAILED during lifecycle execution";
    return (
      <Card
        className="border-muted-foreground/30 bg-muted/[0.04]"
        data-testid="polymarket-empty-rejected"
      >
        <CardContent className="space-y-1.5 p-3 text-xs">
          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            submission skipped
          </p>
          <p className="text-foreground/85">
            No Polymarket market was created for this event — the quality
            panel verdict was <span className="font-mono">{reason}</span>.
          </p>
          <p className="font-mono text-[10px] text-muted-foreground">
            mode · {DASH} · market_id · {DASH} · builder_code · {DASH}
          </p>
        </CardContent>
      </Card>
    );
  }

  const idx = (event.phases ?? []).indexOf(phase);
  const det = detailsAt(event.phases, idx >= 0 ? idx : 5);
  const marketId = event.polymarket?.marketId ?? det.market_id ?? event.market_id;
  const marketUrl = event.polymarket?.marketUrl ?? det.market_url ?? event.market_url;
  // Only fall back to a derived simulated flag when *some* polymarket-ish
  // signal exists; if the entire submission row is null we leave the mode as
  // a dash rather than fabricating "real" / "MOCK badge".
  const hasAnySubmission =
    event.polymarket != null
    || det.market_id != null
    || event.market_id != null;
  const isSimulated: boolean | null =
    event.polymarket?.isSimulated
      ?? (det.is_simulated as boolean | undefined)
      ?? (event.is_simulated as boolean | undefined)
      ?? (hasAnySubmission ? false : null);
  const mode: string =
    event.polymarket?.mode
    ?? (isSimulated === true ? "dry_run" : isSimulated === false ? "real" : DASH);
  const builderCode = event.polymarket?.builderCode ?? DASH;
  const feesEstimate = event.polymarket?.feesEstimateUsdc;
  const payload = (event.polymarket?.payload ?? null) as PolymarketPayload | null;

  return (
    <div className="space-y-3">
      <IOSection
        inputs={[
          {
            label: "question payload",
            value: payload ? (
              <PayloadFieldsList payload={payload} />
            ) : (
              <span className="font-mono text-[10px] text-muted-foreground">
                Awaiting submission…
              </span>
            ),
          },
          {
            label: "builder_code",
            value: <span className="font-mono">{builderCode}</span>,
          },
          {
            label: "mode",
            value: (
              <Badge
                className={cn(
                  "font-mono text-[9px] uppercase tracking-wider",
                  MODE_BADGE_CLASSES[mode] ??
                    "border-border/60 bg-muted/10 text-muted-foreground",
                )}
              >
                {mode}
              </Badge>
            ),
          },
          ...(feesEstimate !== null && feesEstimate !== undefined
            ? [
                {
                  label: "fees_estimate_usdc",
                  value: (
                    <span className="font-mono text-foreground/85">
                      {formatUsd(feesEstimate)}
                    </span>
                  ),
                },
              ]
            : []),
        ]}
        outputs={[
          {
            label: "market_id",
            value: marketId ? <span className="font-mono">{marketId}</span> : DASH,
          },
          {
            label: "market_url",
            value: marketUrl ? (
              isSimulated ? (
                <span className="font-mono text-muted-foreground">{marketUrl}</span>
              ) : (
                <a
                  href={marketUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-1 font-mono text-primary hover:underline"
                >
                  open market
                  <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              )
            ) : (
              DASH
            ),
          },
        ]}
      />
      <PayloadJsonViewer payload={event.polymarket?.payload ?? null} />
      <div className="space-y-1 text-xs">
        <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          safety gates
        </p>
        <ul className="grid grid-cols-2 gap-1 font-mono text-[10px] text-foreground/85 sm:grid-cols-4">
          <li className="rounded border border-border/60 bg-muted/10 px-2 py-1">
            rate-limit
          </li>
          <li className="rounded border border-border/60 bg-muted/10 px-2 py-1">
            idempotency
          </li>
          <li className="rounded border border-border/60 bg-muted/10 px-2 py-1">
            quality gate
          </li>
          <li className="rounded border border-border/60 bg-muted/10 px-2 py-1">
            real-mode confirm
          </li>
        </ul>
      </div>
    </div>
  );
}

function PayloadFieldsList({ payload }: { payload: PolymarketPayload }) {
  const rows: { label: string; value: ReactNode }[] = [
    {
      label: "question",
      value: payload.question ? (
        <span dir="auto" className="text-foreground/90">
          {payload.question}
        </span>
      ) : (
        DASH
      ),
    },
    {
      label: "outcomes",
      value:
        payload.outcomes && payload.outcomes.length > 0 ? (
          <span className="flex flex-wrap gap-1">
            {payload.outcomes.map((outcome, i) => (
              <Badge
                key={`${outcome}-${i}`}
                dir="auto"
                className="border-border/60 bg-muted/10 font-mono text-[10px] text-foreground/85"
              >
                {outcome}
              </Badge>
            ))}
          </span>
        ) : (
          DASH
        ),
    },
    {
      label: "resolution_source",
      value: payload.resolution_source ? (
        <span dir="auto" className="font-mono text-foreground/85">
          {payload.resolution_source}
        </span>
      ) : (
        DASH
      ),
    },
    {
      label: "end_date (UTC)",
      value: (
        <span className="font-mono text-foreground/85">
          {formatEndDateUtc(payload.end_date_iso)}
        </span>
      ),
    },
    {
      label: "initial_liquidity_usdc",
      value:
        payload.initial_liquidity_usdc !== undefined &&
        payload.initial_liquidity_usdc !== null ? (
          <span className="font-mono text-foreground/85">
            {formatUsd(payload.initial_liquidity_usdc)}
          </span>
        ) : (
          DASH
        ),
    },
    {
      label: "external_id",
      value: payload.external_id ? (
        <span className="font-mono text-foreground/85">{payload.external_id}</span>
      ) : (
        DASH
      ),
    },
    {
      label: "builder_name",
      value: payload.builder_name ? (
        <span dir="auto" className="text-foreground/85">
          {payload.builder_name}
        </span>
      ) : (
        DASH
      ),
    },
    {
      label: "builder_code",
      value: payload.builder_code ? (
        <span className="font-mono text-foreground/85">{payload.builder_code}</span>
      ) : (
        DASH
      ),
    },
    {
      label: "category",
      value: payload.category ? (
        <span dir="auto" className="text-foreground/85">
          {payload.category}
        </span>
      ) : (
        DASH
      ),
    },
  ];
  return (
    <dl className="space-y-1.5">
      {rows.map((row) => (
        <div key={row.label} className="flex flex-col gap-0.5">
          <dt className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
            {row.label}
          </dt>
          <dd className="text-xs">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function PayloadJsonViewer({
  payload,
}: {
  payload: Record<string, unknown> | null;
}) {
  if (!payload) return null;
  return (
    <details className="rounded-md border border-border/40 bg-card/40">
      <summary className="cursor-pointer list-none px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        View API Payload
      </summary>
      <pre
        aria-label="Raw Polymarket Gamma API payload"
        className="max-h-72 overflow-auto rounded-b-md border-t border-border/40 bg-background/40 p-3 font-mono text-[10px] leading-relaxed text-foreground/80"
      >
        {JSON.stringify(payload, null, 2)}
      </pre>
    </details>
  );
}

// ─── Revenue details ──────────────────────────────────────────────────────

function RevenueDetails({ event }: { event: EventDetail }) {
  // Streaming revenue only exists when the market was created. For REJECTED /
  // FAILED events there is nothing to stream, so explain that rather than
  // showing the misleading "Awaiting first fill" copy.
  const status = String(event.status ?? "").toUpperCase();
  const submissionSkipped =
    event.polymarket == null && (status === "REJECTED" || status === "FAILED");
  if (submissionSkipped) {
    return (
      <Card
        className="border-muted-foreground/30 bg-muted/[0.04]"
        data-testid="revenue-empty-rejected"
      >
        <CardContent className="space-y-1.5 p-3 text-xs">
          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            streaming skipped
          </p>
          <p className="text-foreground/85">
            No streaming revenue — the Polymarket market was never created
            because the event was{" "}
            <span className="font-mono">{status}</span>.
          </p>
          <p className="font-mono text-[10px] text-muted-foreground">
            cumulative fee · {DASH} · entries · {DASH} · last fill · {DASH}
          </p>
        </CardContent>
      </Card>
    );
  }

  const marketId = event.polymarket?.marketId ?? event.market_id;
  const stream = event.polymarket?.revenueStream ?? [];
  const total = stream.reduce((acc, row) => acc + (row.usd ?? 0), 0);
  const hasStream = stream.length > 0;
  return (
    <div className="space-y-3">
      <IOSection
        inputs={[
          {
            label: "market_id",
            value: marketId ? <span className="font-mono">{marketId}</span> : DASH,
          },
          { label: "fill events", value: "Polygon `MarketFilled` logs" },
        ]}
        outputs={[
          {
            label: "builder_fee_events rows",
            value: hasStream ? `${stream.length} entries` : DASH,
          },
          {
            label: "cumulative fee",
            value: total > 0 ? formatUsd(total) : "$0.00",
          },
        ]}
      />
      {hasStream ? (
        <Card className="border-border/60">
          <CardContent className="space-y-2 p-3">
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              revenue stream · per-leg disbursement
            </p>
            <ul
              className="divide-y divide-border/40 rounded-md border border-border/40 bg-card/40"
              aria-label="Builder fee disbursement legs"
            >
              {stream.map((leg, i) => (
                <li
                  key={`${leg.arcTxHash ?? leg.recipient ?? "leg"}-${i}`}
                  className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 text-xs"
                >
                  <span
                    className="font-mono text-foreground/85"
                    title={leg.recipient ?? undefined}
                  >
                    {shortAddr(leg.recipient ?? null)}
                  </span>
                  <span className="font-mono text-emerald-400">
                    {formatUsd(leg.usd)}
                  </span>
                  {leg.arcTxHash ? (
                    <a
                      href={arcTxUrl(leg.arcTxHash)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 font-mono text-[10px] text-primary hover:underline"
                      aria-label={`Arc transaction ${leg.arcTxHash}`}
                    >
                      {leg.arcTxHash.slice(0, 10)}…
                      <ExternalLink className="h-3 w-3" aria-hidden />
                    </a>
                  ) : (
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {DASH}
                    </span>
                  )}
                  {leg.isSimulated !== undefined && (
                    <Badge
                      className={cn(
                        "font-mono text-[9px] uppercase tracking-wider",
                        leg.isSimulated
                          ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
                          : "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
                      )}
                    >
                      {leg.isSimulated ? "sim" : "real"}
                    </Badge>
                  )}
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-baseline justify-between gap-2 pt-1">
              <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                Entries: {stream.length}
              </span>
              <span className="font-mono text-foreground/90">
                Total disbursed: {formatUsd(total)}
              </span>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card className="border-border/60">
          <CardContent className="p-3 text-xs">
            <p className="font-mono text-[10px] text-muted-foreground">
              Awaiting first fill — fees stream after Polymarket market resolution
            </p>
          </CardContent>
        </Card>
      )}
      <Card className="border-border/60">
        <CardContent className="space-y-1 p-3 text-xs">
          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            split formula · BuilderFeeRouter.record_fill_with_split
          </p>
          <p className="font-mono text-[10px] text-foreground/85">
            fee · 90 % → winner_address · 10 % → treasury
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
