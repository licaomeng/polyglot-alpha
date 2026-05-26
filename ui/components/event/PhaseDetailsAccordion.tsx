"use client";

import { useState, useCallback, type ReactNode } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { IngestionSourcesView } from "./IngestionSourcesView";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { EventDetail, PhaseState } from "@/lib/api";

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
  return (
    <div className="space-y-3">
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

function JudgeDetails({ event }: { event: EventDetail }) {
  const verdict = event.overallVerdict;
  const reasoning = event.overallReasoning;
  return (
    <div className="space-y-3">
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
      {verdict && (
        <div
          className={cn(
            "rounded-md border p-3 text-xs",
            verdict === "FAIL"
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
            value: <span className="font-mono">{event.builder_code ?? "polyglot_alpha"}</span>,
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

function PolymarketDetails({
  event,
  phase,
}: {
  event: EventDetail;
  phase: PhaseState;
}) {
  const idx = (event.phases ?? []).indexOf(phase);
  const det = detailsAt(event.phases, idx >= 0 ? idx : 5);
  const marketId = event.polymarket?.marketId ?? det.market_id ?? event.market_id;
  const marketUrl = event.polymarket?.marketUrl ?? det.market_url ?? event.market_url;
  const isSimulated =
    event.polymarket?.isSimulated ?? det.is_simulated ?? event.is_simulated ?? false;
  const mode = isSimulated ? "dry_run" : "real";
  return (
    <div className="space-y-3">
      <IOSection
        inputs={[
          { label: "question payload", value: "title + outcomes + resolution_source + cutoff" },
          {
            label: "builder_code",
            value: <span className="font-mono">{event.builder_code ?? "polyglot_alpha"}</span>,
          },
          {
            label: "mode",
            value: (
              <Badge
                className={cn(
                  "font-mono text-[9px] uppercase tracking-wider",
                  isSimulated
                    ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
                    : "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
                )}
              >
                {mode}
              </Badge>
            ),
          },
        ]}
        outputs={[
          {
            label: "market_id",
            value: marketId ? <span className="font-mono">{marketId}</span> : "—",
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
              "—"
            ),
          },
        ]}
      />
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

// ─── Revenue details ──────────────────────────────────────────────────────

function RevenueDetails({ event }: { event: EventDetail }) {
  const marketId = event.polymarket?.marketId ?? event.market_id;
  const stream = event.polymarket?.revenueStream ?? [];
  const total = stream.reduce((acc, row) => acc + (row.usd ?? 0), 0);
  return (
    <div className="space-y-3">
      <IOSection
        inputs={[
          {
            label: "market_id",
            value: marketId ? <span className="font-mono">{marketId}</span> : "—",
          },
          { label: "fill events", value: "Polygon `MarketFilled` logs" },
        ]}
        outputs={[
          {
            label: "builder_fee_events rows",
            value: stream.length > 0 ? `${stream.length} entries` : "—",
          },
          {
            label: "cumulative fee",
            value: total > 0 ? `$${total.toFixed(2)}` : "$0.00",
          },
        ]}
      />
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
