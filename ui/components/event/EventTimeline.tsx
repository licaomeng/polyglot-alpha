"use client";

import { useMemo } from "react";
import { PhaseCard } from "./PhaseCard";
import type { EventDetail, JudgeScore, PhaseState, PhaseStatus } from "@/lib/api";
import { SUB_PHASES_BY_PHASE } from "@/lib/api";
import { BidTable } from "@/components/auction/BidTable";
import { PipelineLayerCard } from "@/components/pipeline/PipelineLayerCard";
import { JudgePanel } from "@/components/judge/JudgePanel";
import { TxLink } from "@/components/onchain/TxLink";
import { ContractAddressDisplay } from "@/components/onchain/ContractAddressDisplay";
import { BuilderFeeStream } from "@/components/polymarket/BuilderFeeStream";
import { PolymarketDetail } from "@/components/polymarket/PolymarketDetail";
import { Separator } from "@/components/ui/separator";
import { ExternalLink } from "lucide-react";
import { usePhaseState } from "@/hooks/usePhaseState";

// Map the backend detail row for phase 4 (with verdict + overall_score) into
// a synthetic 11-judge array when the top-level `judges` field is absent.
// Backend currently exposes:
//   event.translation_scores: { bleu, comet, mqm, ... }
//   event.style_alignment_passes: { D1: bool, D2: bool, ... }
// We surface those values here so the UI can render even before backend
// emits a full `judges` array.
// Coerce a backend translation_scores entry into a 0..1 JudgeScore for the
// UI. The MQM entry comes back as an object ({ score: 78, major_count, ... })
// because the panel reports the raw 0-100 score plus error counts; everything
// else is either a plain float or null when the model is unreachable.
function coerceTranslationScore(
  key: string,
  raw: unknown,
): { score: number; passed?: boolean } {
  if (raw == null) return { score: 0 };
  if (typeof raw === "number") {
    // BLEU is reported on the 0-100 scale (e.g. 27.3) and COMET in 0-1.
    if (key.toLowerCase() === "bleu") {
      return { score: raw > 1 ? Math.min(1, raw / 100) : raw };
    }
    return { score: raw };
  }
  if (typeof raw === "object") {
    const obj = raw as { score?: unknown; major_count?: unknown };
    if (typeof obj.score === "number") {
      const norm = obj.score > 1 ? obj.score / 100 : obj.score;
      const passed =
        typeof obj.major_count === "number" ? obj.major_count === 0 : undefined;
      return { score: norm, passed };
    }
  }
  return { score: 0 };
}

function deriveJudges(event: EventDetail): JudgeScore[] | undefined {
  if (Array.isArray(event.judges) && event.judges.length > 0) return event.judges;
  type Loose = EventDetail & {
    translation_scores?: Record<string, unknown>;
    style_alignment_passes?: Record<string, boolean>;
  };
  const loose = event as Loose;
  const out: JudgeScore[] = [];
  if (loose.translation_scores) {
    for (const [k, v] of Object.entries(loose.translation_scores)) {
      const { score, passed } = coerceTranslationScore(k, v);
      out.push({
        judge: k.toUpperCase(),
        score,
        passed,
        category: "translation",
      });
    }
  }
  if (loose.style_alignment_passes) {
    for (const [k, passed] of Object.entries(loose.style_alignment_passes)) {
      out.push({
        judge: k.toUpperCase(),
        score: passed ? 1.0 : 0.0,
        passed: Boolean(passed),
        category: "style",
      });
    }
  }
  return out.length ? out : undefined;
}

interface PhaseDetails {
  tx_hash?: string;
  market_id?: string;
  market_url?: string;
  is_simulated?: boolean;
  question_id?: string;
  builder_code?: string;
  pipeline_trace_ipfs?: string;
  winner_address?: string;
  reasoning_ipfs?: string;
}

function detailsAt(phases: PhaseState[], idx: number): PhaseDetails {
  const p = phases[idx];
  return (p?.details ?? {}) as PhaseDetails;
}

export function EventTimeline({ event }: { event: EventDetail }) {
  const phases = useMemo(() => event.phases ?? [], [event.phases]);
  const judges = useMemo(() => deriveJudges(event), [event]);
  const { activePhase } = usePhaseState();

  return (
    <ol className="relative space-y-4 border-l-2 border-border pl-6">
      {phases.map((phase, idx) => (
        <li key={`${phase.name}-${idx}`} className="relative">
          <span
            className={
              phase.status === "running"
                ? "absolute -left-[34px] top-6 grid h-5 w-5 place-items-center rounded-full border-2 border-background bg-primary/15 ring-2 ring-primary/40"
                : phase.status === "completed"
                  ? "absolute -left-[34px] top-6 grid h-5 w-5 place-items-center rounded-full border-2 border-background bg-emerald-500/15 ring-1 ring-emerald-400/40"
                  : phase.status === "failed"
                    ? "absolute -left-[34px] top-6 grid h-5 w-5 place-items-center rounded-full border-2 border-background bg-destructive/15 ring-1 ring-destructive/40"
                    : "absolute -left-[34px] top-6 grid h-5 w-5 place-items-center rounded-full border-2 border-background bg-muted/40 ring-1 ring-muted-foreground/30"
            }
            aria-hidden
          >
            <span
              className={
                phase.status === "running"
                  ? "h-2.5 w-2.5 animate-pulse rounded-full bg-primary"
                  : phase.status === "completed"
                    ? "h-2.5 w-2.5 rounded-full bg-emerald-400"
                    : phase.status === "failed"
                      ? "h-2.5 w-2.5 rounded-full bg-destructive"
                      : "h-2.5 w-2.5 rounded-full bg-muted-foreground/60"
              }
            />
          </span>
          <PhaseCard phase={phase} index={idx}>
            {renderPhaseBody(phase, idx, event, judges)}
          </PhaseCard>
          {/* Hidden indicator for tests / a11y: reflects the shared active state */}
          {activePhase === idx && <span className="sr-only">phase active</span>}
        </li>
      ))}
    </ol>
  );
}

function renderPhaseBody(
  phase: PhaseState,
  idx: number,
  event: EventDetail,
  judges: JudgeScore[] | undefined,
) {
  const phases = event.phases ?? [];
  const eventId = event.id;

  switch (phase.name) {
    case "Event Ingestion":
      return (
        <div className="space-y-2 text-xs text-muted-foreground">
          <p>
            <span className="text-foreground/80">Source:</span> {event.source}
          </p>
          <p>
            <span className="text-foreground/80">Headline:</span> {event.headline}
          </p>
        </div>
      );

    case "USDC Auction": {
      const auctionDetails = detailsAt(phases, idx);
      const bids = event.bids ?? [];
      return (
        <div className="space-y-2">
          {bids.length > 0 ? (
            <BidTable bids={bids} />
          ) : (
            <p className="text-xs text-muted-foreground">no bids recorded</p>
          )}
          {auctionDetails.tx_hash && (
            <div className="pt-1">
              <TxLink txHash={auctionDetails.tx_hash} mode="live" label="settle tx" />
            </div>
          )}
        </div>
      );
    }

    case "Translation Pipeline": {
      const translationDetails = detailsAt(phases, idx);
      const t = event.translation;
      const subPhases = (phase.details?.subPhases as
        | Record<string, PhaseStatus>
        | undefined) ?? undefined;
      const subPhaseChips = (
        <SubPhaseChips phaseIndex={idx} phase={phase} subPhases={subPhases} />
      );
      if (!t) {
        return (
          <div className="space-y-3">
            {subPhaseChips}
            <div className="space-y-1 text-xs text-muted-foreground">
              <p>winner · <span className="font-mono">{translationDetails.winner_address ?? "—"}</span></p>
              {translationDetails.pipeline_trace_ipfs && (
                <a
                  href={`https://ipfs.io/ipfs/${translationDetails.pipeline_trace_ipfs}`}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-1 font-mono text-primary hover:underline"
                >
                  ipfs · {translationDetails.pipeline_trace_ipfs.slice(0, 14)}…
                  <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              )}
            </div>
          </div>
        );
      }
      return (
        <div className="space-y-3">
          {subPhaseChips}
          <PipelineLayerCard translation={t} />
        </div>
      );
    }

    case "11-Judge Panel": {
      if (!judges) {
        return <p className="text-xs text-muted-foreground">awaiting verdict</p>;
      }
      return (
        <JudgePanel
          judges={judges}
          verdict={event.overallVerdict ?? (event as { verdict?: string }).verdict}
          reasoning={event.overallReasoning}
        />
      );
    }

    case "On-chain Anchor": {
      const onchain = detailsAt(phases, idx);
      const anchor = event.anchor;
      const txHash = anchor?.txHash ?? onchain.tx_hash;
      if (!txHash && !anchor?.contractAddress) {
        return <p className="text-xs text-muted-foreground">awaiting anchor commit</p>;
      }
      return (
        <div className="space-y-2 text-xs">
          {txHash && (
            <TxLink
              txHash={txHash}
              url={anchor?.explorerUrl}
              mode={event.mode === "live" ? "live" : "mock"}
              label="anchor tx"
            />
          )}
          {anchor?.contractAddress && (
            <ContractAddressDisplay
              address={anchor.contractAddress}
              label="contract"
            />
          )}
          {anchor?.block !== undefined && (
            <p className="text-muted-foreground">
              Block <span className="font-mono text-foreground/80">#{anchor.block}</span>
            </p>
          )}
          {(anchor?.ipfsCid ?? onchain.reasoning_ipfs) && (
            <a
              href={`https://ipfs.io/ipfs/${anchor?.ipfsCid ?? onchain.reasoning_ipfs}`}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-1 font-mono text-primary hover:underline"
            >
              reasoning · ipfs · {String(anchor?.ipfsCid ?? onchain.reasoning_ipfs).slice(0, 14)}…
              <ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          )}
        </div>
      );
    }

    case "Polymarket V2 Submission": {
      const pmDetails = detailsAt(phases, idx);
      // Backend exposes `builder_code` at the top level of `EventDetail`
      // (snake_case) in addition to the phase-level details payload, so we
      // also consult that field as a fallback before falling back to the
      // literal "polyglot_alpha" placeholder.
      const topLevelBuilderCode = event.builder_code;
      const topLevelMarketId = event.market_id;
      const topLevelMarketUrl = event.market_url;
      const topLevelIsSimulated = event.is_simulated;
      // Merge phase-level details into the top-level polymarket object so we
      // always have a market_url/builder_code to render — even when the
      // canonical `event.polymarket` is missing.
      const merged = {
        builderCode:
          event.polymarket?.builderCode ??
          pmDetails.builder_code ??
          topLevelBuilderCode ??
          "polyglot_alpha",
        marketId:
          event.polymarket?.marketId ?? pmDetails.market_id ?? topLevelMarketId,
        marketUrl:
          event.polymarket?.marketUrl ?? pmDetails.market_url ?? topLevelMarketUrl,
        submissionTx: event.polymarket?.submissionTx,
        isSimulated:
          event.polymarket?.isSimulated ??
          pmDetails.is_simulated ??
          topLevelIsSimulated,
        mode: event.polymarket?.mode,
        status: event.polymarket?.status,
        payload: event.polymarket?.payload,
        revenueStream: event.polymarket?.revenueStream ?? [],
      };
      return <PolymarketDetail polymarket={merged} eventId={eventId} />;
    }

    case "Streaming Revenue": {
      const stream = event.polymarket?.revenueStream;
      const fills = event.polymarket?.recentFills;
      if (!stream || stream.length === 0) {
        return (
          <p className="text-xs text-muted-foreground">no fees streamed yet</p>
        );
      }
      return (
        <>
          <Separator />
          <BuilderFeeStream stream={stream} recentFills={fills} />
        </>
      );
    }

    default:
      return null;
  }
}

/**
 * Progressive-disclosure chips for the agent-debate sub-phases nested under
 * phase 2. Each chip flips from `pending → running → completed` as the
 * matching SSE event (`translation.completed`, `critic.completed`,
 * `moderator.verdict`, `refine.completed`) arrives.
 */
function SubPhaseChips({
  phaseIndex,
  phase,
  subPhases,
}: {
  phaseIndex: number;
  phase: PhaseState;
  subPhases: Record<string, PhaseStatus> | undefined;
}) {
  const names = SUB_PHASES_BY_PHASE[phaseIndex];
  if (!names || names.length === 0) return null;

  // Infer per-chip status: explicit override → otherwise derive from parent
  // phase state (parent `completed` ⇒ all chips completed; parent `pending`
  // ⇒ all pending; otherwise the first chip runs).
  const inferred = (name: string, idx: number): PhaseStatus => {
    const explicit = subPhases?.[name];
    if (explicit) return explicit;
    if (phase.status === "completed") return "completed";
    if (phase.status === "failed" && idx === 0) return "failed";
    if (phase.status === "running" && idx === 0) return "running";
    return "pending";
  };

  return (
    <ol
      className="flex flex-wrap gap-1.5"
      aria-label="Translation pipeline sub-phases"
      data-testid="sub-phase-chips"
    >
      {names.map((name, idx) => {
        const status = inferred(name, idx);
        const tone =
          status === "completed"
            ? "border-emerald-500/40 bg-emerald-500/[0.06] text-emerald-300"
            : status === "running"
              ? "border-primary/50 bg-primary/[0.06] text-primary"
              : status === "failed"
                ? "border-destructive/50 bg-destructive/[0.06] text-destructive"
                : "border-border/60 bg-muted/20 text-muted-foreground";
        return (
          <li
            key={name}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider ${tone}`}
          >
            <span
              className={
                status === "running"
                  ? "h-1.5 w-1.5 animate-pulse rounded-full bg-primary"
                  : status === "completed"
                    ? "h-1.5 w-1.5 rounded-full bg-emerald-400"
                    : status === "failed"
                      ? "h-1.5 w-1.5 rounded-full bg-destructive"
                      : "h-1.5 w-1.5 rounded-full bg-muted-foreground/60"
              }
              aria-hidden
            />
            {name}
          </li>
        );
      })}
    </ol>
  );
}
