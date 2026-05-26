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
import { PhaseInfo } from "@/components/event/MetricExplainer";
import { ProgressIndicator } from "@/components/event/ProgressIndicator";
import { PhaseDetailsAccordion } from "@/components/event/PhaseDetailsAccordion";
import { classifyIpfsRef, formatUsd, shortAddr } from "@/lib/utils";

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
  // ``event.judges`` may now be the new dossier shape (carries
  // ``panelBudgetExceeded``) rather than the legacy ``JudgeScore[]``. The
  // timeline only consumes ``judge``/``score``/``category``/``passed`` so
  // when the dossier is present we re-shape it; when the array is the
  // legacy shape we return it as-is.
  const rawJudges = event.judges;
  if (Array.isArray(rawJudges) && rawJudges.length > 0) {
    const first = rawJudges[0] as unknown as Record<string, unknown>;
    const isDossier =
      typeof first.name === "string" && typeof first.passed === "boolean";
    if (isDossier) {
      return (rawJudges as unknown as Array<Record<string, unknown>>).map(
        (j) => ({
          judge: String(j.name ?? "").toUpperCase(),
          score: typeof j.score === "number" ? j.score : 0,
          passed: typeof j.passed === "boolean" ? j.passed : undefined,
          category: String(j.name ?? "").toLowerCase().startsWith("d")
            ? "style"
            : "translation",
        }),
      );
    }
    return rawJudges as JudgeScore[];
  }
  type Loose = EventDetail & {
    translation_scores?: Record<string, unknown> | null;
    style_alignment_passes?: Record<string, boolean> | null;
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

/**
 * Per-phase documentation rendered through the (i) tooltip. Each entry
 * surfaces (a) what the phase does, (b) which SSE event signals its
 * progress, and (c) what the phase persists to disk/db on completion.
 */
const PHASE_DOCS: Record<string, { what: string; signals: string; produces: string; typicalSec: number }> = {
  "Event Ingestion": {
    what: "Polls RSS / manual triggers and stages a news event for the marketplace.",
    signals: "event.created / event.updated",
    produces: "events row (headline, sources, content_hash)",
    typicalSec: 5,
  },
  "USDC Auction": {
    what: "Open-bid auction where reference seeders + operators submit (bid, candidate_hash, stake).",
    signals: "auction.opened → bid.submitted → auction.settled",
    produces: "bids table + auction_winner; on-chain settle tx",
    typicalSec: 30,
  },
  "Translation Pipeline": {
    what: "Winner runs L1 Analysts → L2 Translators → L3 Critics → L4 Moderator → L5 Refine to produce the final question.",
    signals: "translation.completed → critic.completed → moderator.verdict → refine.completed",
    produces: "translations row (source, target, final_question)",
    typicalSec: 60,
  },
  "11-Judge Panel": {
    what: "3 translation judges (BLEU / COMET / MQM) + 8 style judges (D1–D8). Run in parallel via asyncio.gather.",
    signals: "quality.verdict",
    produces: "quality_scores row with verdict (PASS / BORDERLINE / FAIL) + overall_score in [0,1]",
    typicalSec: 60,
  },
  "On-chain Anchor": {
    what: "SHA256(candidate) committed to the Arc testnet — gives external verifiability of the question.",
    signals: "onchain.committed",
    produces: "anchors row (tx_hash, block, ipfs_cid?)",
    typicalSec: 15,
  },
  "Polymarket V2 Submission": {
    what: "Final question submitted to Polymarket V2 with builder_code so resolved markets stream builder fees back.",
    signals: "polymarket.submitted",
    produces: "polymarket_submissions row (market_id, market_url, is_simulated)",
    typicalSec: 10,
  },
  "Streaming Revenue": {
    what: "Builder fees stream in as the Polymarket market gets filled.",
    signals: "builder_fee.accrued → event.finalized",
    produces: "builder_fees rows; events.status → SETTLED",
    typicalSec: 60,
  },
};

function PhaseBodyHeader({ phase }: { phase: PhaseState }) {
  const doc = PHASE_DOCS[phase.name];
  if (!doc) return null;
  return (
    <div className="flex flex-col gap-2 border-b border-border/40 pb-3">
      <div className="flex items-center gap-1.5">
        <PhaseInfo
          title={phase.name}
          what={doc.what}
          signals={doc.signals}
          produces={doc.produces}
        />
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          hover the (i) for what this phase does
        </span>
      </div>
      {(phase.status === "running" || phase.status === "completed" || phase.status === "pending") && (
        <ProgressIndicator
          phaseName={phase.name}
          status={phase.status}
          startedAt={phase.startedAt}
          completedAt={phase.completedAt}
          typicalSeconds={doc.typicalSec}
        />
      )}
    </div>
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
  const header = <PhaseBodyHeader phase={phase} />;
  const accordion = (
    <PhaseDetailsAccordion phase={phase} index={idx} event={event} />
  );
  const wrap = (body: React.ReactNode) => (
    <div className="space-y-3">
      {header}
      {body}
      {accordion}
    </div>
  );

  switch (phase.name) {
    case "Event Ingestion":
      return wrap(
        <div className="space-y-2 text-xs text-muted-foreground">
          <p>
            <span className="text-foreground/80">Source:</span>{" "}
            <span dir="auto">{event.source}</span>
          </p>
          <p>
            <span className="text-foreground/80">Headline:</span>{" "}
            <span dir="auto">{event.headline}</span>
          </p>
        </div>,
      );

    case "USDC Auction": {
      const auctionDetails = detailsAt(phases, idx);
      const bids = event.bids ?? [];
      return wrap(
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
        </div>,
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
        return wrap(
          <div className="space-y-3">
            {subPhaseChips}
            <div className="space-y-1 text-xs text-muted-foreground">
              <p>winner · <span className="font-mono">{translationDetails.winner_address ?? "—"}</span></p>
              {translationDetails.pipeline_trace_ipfs && (() => {
                // Real CIDs render as a gateway link; synthetic refs (e.g.
                // `ipfs://pipeline/qwen/...`) render as muted text since the
                // ipfs.io gateway would 404 on them.
                const ref = classifyIpfsRef(
                  String(translationDetails.pipeline_trace_ipfs),
                );
                if (!ref) return null;
                if (ref.isReal && ref.gatewayUrl) {
                  return (
                    <a
                      href={ref.gatewayUrl}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-flex items-center gap-1 font-mono text-primary hover:underline"
                    >
                      ipfs · {ref.cid.slice(0, 14)}…
                      <ExternalLink className="h-3 w-3" aria-hidden />
                    </a>
                  );
                }
                return (
                  <span
                    className="inline-flex items-center gap-1 font-mono text-muted-foreground"
                    title={`Synthetic provenance: ${ref.cid}`}
                  >
                    synthetic · {ref.cid.slice(0, 24)}…
                  </span>
                );
              })()}
            </div>
          </div>,
        );
      }
      return wrap(
        <div className="space-y-3">
          {subPhaseChips}
          <PipelineLayerCard translation={t} />
        </div>,
      );
    }

    case "11-Judge Panel": {
      if (!judges) {
        return wrap(
          <p className="text-xs text-muted-foreground">awaiting verdict</p>,
        );
      }
      return wrap(
        <JudgePanel
          judges={judges}
          verdict={event.overallVerdict ?? (event as { verdict?: string }).verdict}
          reasoning={event.overallReasoning}
        />,
      );
    }

    case "On-chain Anchor": {
      const onchain = detailsAt(phases, idx);
      const anchor = event.anchor;
      const txHash = anchor?.txHash ?? onchain.tx_hash;
      if (!txHash && !anchor?.contractAddress) {
        return wrap(
          <p className="text-xs text-muted-foreground">awaiting anchor commit</p>,
        );
      }
      return wrap(
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
          {(anchor?.ipfsCid ?? onchain.reasoning_ipfs) && (() => {
            // Real v0/v1 CIDs render as a clickable gateway link; synthetic
            // pipeline refs (`ipfs://pipeline/...`, `ipfs://mock/...`) render
            // as a muted, non-clickable label so the user isn't sent to a 404.
            const rawCid = String(anchor?.ipfsCid ?? onchain.reasoning_ipfs);
            const ref = classifyIpfsRef(rawCid);
            if (!ref) return null;
            if (ref.isReal && ref.gatewayUrl) {
              return (
                <a
                  href={ref.gatewayUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-1 font-mono text-primary hover:underline"
                >
                  reasoning · ipfs · {ref.cid.slice(0, 14)}…
                  <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              );
            }
            // Preserve the `ipfs://` scheme so the synthetic provenance
            // string is unambiguous (and so DOM probes that look for the
            // literal `ipfs://sim/<hash>` token can find it). The classifier
            // already stripped the prefix to derive `ref.cid`, so we put it
            // back here for display purposes only.
            const displayRef = rawCid.startsWith("ipfs://")
              ? rawCid
              : `ipfs://${ref.cid}`;
            return (
              <span
                className="inline-flex items-center gap-1 font-mono text-muted-foreground"
                title={`Synthetic provenance: ${displayRef}`}
                data-testid="phase-5-ipfs-synthetic"
              >
                reasoning · synthetic · {displayRef.slice(0, 32)}…
              </span>
            );
          })()}
        </div>,
      );
    }

    case "Polymarket V2 Submission": {
      // When the event was REJECTED by the judge panel (or FAILED in the
      // pipeline) no Polymarket submission was attempted. Surface that
      // explicitly so reviewers don't see a contradictory MOCK chip + market
      // placeholder for a market that doesn't exist.
      const overallStatus = String(event.status ?? "").toUpperCase();
      const submissionSkipped =
        event.polymarket == null &&
        (overallStatus === "REJECTED" || overallStatus === "FAILED");
      if (submissionSkipped) {
        const reason =
          overallStatus === "REJECTED"
            ? "REJECTED by the 11-judge panel"
            : "FAILED during lifecycle execution";
        return wrap(
          <div
            data-testid="polymarket-empty-rejected"
            className="rounded-md border border-muted-foreground/30 bg-muted/[0.04] p-3 text-xs"
          >
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              submission skipped
            </p>
            <p className="mt-1 text-foreground/85">
              No Polymarket market was created for this event — the quality
              panel verdict was <span className="font-mono">{reason}</span>.
            </p>
            <p className="mt-1 font-mono text-[10px] text-muted-foreground">
              mode · — · market_id · — · builder_code · —
            </p>
          </div>,
        );
      }

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
      return wrap(<PolymarketDetail polymarket={merged} eventId={eventId} />);
    }

    case "Streaming Revenue": {
      const overallStatus = String(event.status ?? "").toUpperCase();
      // Mock-mode hint: the reputation registry is intentionally skipped for
      // mock events (W5-A1), so the phase completes with zero rep deltas.
      // Surface that explicitly so the empty Reputation Update section
      // doesn't look like a regression.
      const mockRepHint = event.mode === "mock" ? (
        <p
          className="font-mono text-[10px] text-muted-foreground"
          data-testid="revenue-mock-rep-hint"
        >
          (mock — not recorded to reputation)
        </p>
      ) : null;
      const streamingSkipped =
        event.polymarket == null &&
        (overallStatus === "REJECTED" || overallStatus === "FAILED");
      if (streamingSkipped) {
        return wrap(
          <div
            data-testid="revenue-empty-rejected"
            className="rounded-md border border-muted-foreground/30 bg-muted/[0.04] p-3 text-xs"
          >
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              streaming skipped
            </p>
            <p className="mt-1 text-foreground/85">
              No streaming revenue — the Polymarket market was never created
              because the event was{" "}
              <span className="font-mono">{overallStatus}</span>.
            </p>
            <p className="mt-1 font-mono text-[10px] text-muted-foreground">
              cumulative fee · — · entries · — · last fill · —
            </p>
            {mockRepHint}
          </div>,
        );
      }
      const stream = event.polymarket?.revenueStream;
      const fills = event.polymarket?.recentFills;
      if (!stream || stream.length === 0) {
        return wrap(
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">no fees streamed yet</p>
            {mockRepHint}
          </div>,
        );
      }
      // BuilderFeeStream expects a strictly-typed { ts: string; usd: number }[]
      // (for the recharts time axis). Filter out null-ts rows that the new
      // backend shape may include before passing the chart data through.
      const chartStream = stream
        .filter((row): row is typeof row & { ts: string } => typeof row.ts === "string")
        .map((row) => ({ ts: row.ts, usd: row.usd }));
      // Mock-mode per-leg breakdown: the backend emits a `revenueStream` with
      // 2 simulated legs (90% winner / 10% treasury) but does NOT populate
      // `recentFills`, so the BuilderFeeStream chart alone leaves the user
      // without recipient / arc tx / total context. Surface those per-leg
      // rows here for mock events; live mode is unaffected because the
      // BuilderFeeStream's recentFills list already covers that surface.
      const showMockLegs = event.mode === "mock" && stream.length > 0;
      return wrap(
        <>
          <Separator />
          <BuilderFeeStream stream={chartStream} recentFills={fills} />
          {showMockLegs && <MockRevenueLegs stream={stream} />}
          {mockRepHint}
        </>,
      );
    }

    default:
      return wrap(null);
  }
}

/**
 * Per-leg builder-fee disbursement breakdown rendered inside Phase 7 for
 * mock-mode events. The backend's mock `revenueStream` populates exactly
 * two synthetic legs (90% winner + 10% treasury) but does NOT emit the
 * `recentFills` collection that the live BuilderFeeStream chart relies on
 * for its bottom row — so without this panel a mock Phase 7 collapses to
 * a near-zero sparkline with no recipient / arc tx context.
 *
 * Live mode is unaffected: this component is only rendered when
 * `event.mode === "mock"` at the call site.
 */
function MockRevenueLegs({
  stream,
}: {
  stream: NonNullable<EventDetail["polymarket"]>["revenueStream"];
}) {
  if (!stream || stream.length === 0) return null;
  const total = stream.reduce((acc, row) => acc + (row.usd ?? 0), 0);
  return (
    <div
      className="space-y-2 rounded-md border border-border/40 bg-card/40 p-3"
      data-testid="revenue-mock-legs"
    >
      <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        revenue stream · per-leg disbursement
      </p>
      <ul
        className="divide-y divide-border/40 rounded-md border border-border/40 bg-background/40"
        aria-label="Builder fee disbursement legs"
      >
        {stream.map((leg, i) => (
          <li
            key={`${leg.arcTxHash ?? leg.recipient ?? "leg"}-${i}`}
            className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 text-xs"
            data-testid="revenue-stream-row"
          >
            <span
              className="font-mono text-foreground/85"
              title={leg.recipient ?? undefined}
            >
              {shortAddr(leg.recipient ?? null)}
            </span>
            <span className="font-mono text-emerald-400">{formatUsd(leg.usd)}</span>
            {leg.arcTxHash ? (
              <span
                className="font-mono text-[10px] text-muted-foreground"
                title={`Arc tx (simulated): ${leg.arcTxHash}`}
              >
                {leg.arcTxHash.slice(0, 14)}…
              </span>
            ) : (
              <span className="font-mono text-[10px] text-muted-foreground">—</span>
            )}
            {leg.isSimulated !== undefined && (
              <span
                className={
                  leg.isSimulated
                    ? "rounded-md border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-amber-300"
                    : "rounded-md border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-emerald-300"
                }
              >
                {leg.isSimulated ? "sim" : "real"}
              </span>
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
      <p className="font-mono text-[10px] text-muted-foreground">
        split · 90% winner · 10% treasury · Arc explorer links suppressed for
        simulated tx
      </p>
    </div>
  );
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
