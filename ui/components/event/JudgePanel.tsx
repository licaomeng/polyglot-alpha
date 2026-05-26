"use client";

import { useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, XCircle, MinusCircle } from "lucide-react";
import { MetricInfo, METRIC_DOCS } from "./MetricExplainer";
import { TxLink } from "@/components/onchain/TxLink";
import { cn, shortAddr } from "@/lib/utils";
import type { EventDetail } from "@/lib/api";

/**
 * Per-judge row used in the 11-judge breakdown. We hand-roll the rows because
 * the existing `components/judge/JudgePanel.tsx` is a more abstract
 * `JudgeScore[]` renderer. This component reads directly from the backend
 * fields the event-detail API exposes (`translation_scores` +
 * `style_alignment_passes`), which is the actual shape served today.
 */

interface TranslationScoreCell {
  key: "BLEU" | "COMET" | "MQM";
  raw: unknown;
  // Whether the value is null/unavailable
  isNull: boolean;
  // Display string (e.g. "78", "0.84", "n/a")
  display: string;
  // PASS/FAIL/N/A
  status: "pass" | "fail" | "na";
  // Whether the judge's verdict tilts the overall result
  weight: number;
}

interface StyleJudgeCell {
  key: "D1" | "D2" | "D3" | "D4" | "D5" | "D6" | "D7" | "D8";
  passed: boolean | undefined;
  isHardGate: boolean;
}

// Translation judge keys — order matters so we can render them side-by-side.
const TRANSLATION_KEYS: TranslationScoreCell["key"][] = ["BLEU", "COMET", "MQM"];
const STYLE_KEYS: StyleJudgeCell["key"][] = [
  "D1",
  "D2",
  "D3",
  "D4",
  "D5",
  "D6",
  "D7",
  "D8",
];

function readTranslationCell(
  key: TranslationScoreCell["key"],
  raw: unknown,
): TranslationScoreCell {
  if (raw == null) {
    return { key, raw, isNull: true, display: "n/a", status: "na", weight: 0 };
  }
  if (typeof raw === "number") {
    if (key === "BLEU") {
      const v = raw;
      return {
        key,
        raw,
        isNull: false,
        display: v.toFixed(1),
        status: v >= 20 ? "pass" : "fail",
        weight: 1,
      };
    }
    if (key === "COMET") {
      return {
        key,
        raw,
        isNull: false,
        display: raw.toFixed(3),
        status: raw >= 0.7 ? "pass" : "fail",
        weight: 1,
      };
    }
    // MQM as flat number
    return {
      key,
      raw,
      isNull: false,
      display: raw.toFixed(0),
      status: raw >= 70 ? "pass" : "fail",
      weight: 1,
    };
  }
  if (typeof raw === "object") {
    const obj = raw as { score?: unknown; major_count?: unknown };
    const score = typeof obj.score === "number" ? obj.score : null;
    const major = typeof obj.major_count === "number" ? obj.major_count : 0;
    if (score === null) {
      return { key, raw, isNull: true, display: "n/a", status: "na", weight: 0 };
    }
    return {
      key,
      raw,
      isNull: false,
      display: score.toFixed(0),
      status: major === 0 && score >= 70 ? "pass" : "fail",
      weight: 1,
    };
  }
  return { key, raw, isNull: true, display: "n/a", status: "na", weight: 0 };
}

interface JudgePanelProps {
  event: EventDetail & {
    translation_scores?: Record<string, unknown> | null;
    style_alignment_passes?: Record<string, boolean> | null;
    verdict?: string;
    overall_score?: number | null;
  };
}

interface DossierJudgeShape {
  name: string;
  passed: boolean;
  score: number;
  reason?: string;
  panelBudgetExceeded?: boolean;
  softSkip?: boolean;
  panelPartial?: boolean;
}

function isDossierShape(x: unknown): x is DossierJudgeShape {
  if (typeof x !== "object" || x === null) return false;
  const j = x as Record<string, unknown>;
  return typeof j.name === "string" && typeof j.passed === "boolean";
}

export function JudgePanel({ event }: JudgePanelProps) {
  // Build a lookup of dossier rows keyed by judge name (lowercased) so we can
  // surface ``panelBudgetExceeded`` / ``softSkip`` flags on the per-cell
  // verdict pills. The backend emits the same names in both the dossier and
  // the legacy translation_scores / style_alignment_passes maps, so reading
  // both keeps the existing tests passing while letting the partial pill
  // override the "BELOW threshold" copy when a judge timed out.
  const dossierByName = useMemo<Map<string, DossierJudgeShape>>(() => {
    const out = new Map<string, DossierJudgeShape>();
    const raw = (event.judges ?? []) as unknown[];
    for (const j of raw) {
      if (isDossierShape(j)) out.set(j.name.toLowerCase(), j);
    }
    return out;
  }, [event.judges]);

  const translationCells = useMemo<TranslationScoreCell[]>(() => {
    const scores =
      (event.translation_scores as Record<string, unknown> | undefined) ?? {};
    return TRANSLATION_KEYS.map((k) =>
      readTranslationCell(k, scores[k.toLowerCase()] ?? scores[k]),
    );
  }, [event.translation_scores]);

  const styleCells = useMemo<StyleJudgeCell[]>(() => {
    const passes =
      (event.style_alignment_passes as Record<string, boolean> | undefined) ?? {};
    return STYLE_KEYS.map((k) => ({
      key: k,
      passed: passes[k.toLowerCase()] ?? passes[k],
      isHardGate: METRIC_DOCS[k]?.gate === "HARD",
    }));
  }, [event.style_alignment_passes]);

  // Highlight the judges whose vote *triggered* the verdict — for FAIL, any
  // judge that failed is a decider; for PASS, hard gates that passed are
  // highlighted (they were the binding constraint).
  const verdict = (event.verdict ?? event.overallVerdict ?? "").toString().toUpperCase();
  const isFail = verdict === "FAIL" || verdict === "REJECTED";
  const overall = event.overall_score ?? null;

  const styleHasFailures = styleCells.some((c) => c.passed === false);
  if (!event.translation_scores && !event.style_alignment_passes) {
    return null;
  }

  const panelPartial =
    Boolean(event.panelPartial)
    || Array.from(dossierByName.values()).some((j) => j.panelBudgetExceeded);
  const partialCount = Array.from(dossierByName.values()).filter(
    (j) => j.panelBudgetExceeded,
  ).length;
  const totalDossier = dossierByName.size;
  const completedCount = totalDossier > 0 ? totalDossier - partialCount : 0;
  const pendingNames =
    Array.isArray(event.pendingJudgeNames) && event.pendingJudgeNames.length > 0
      ? event.pendingJudgeNames
      : Array.from(dossierByName.values())
          .filter((j) => j.panelBudgetExceeded)
          .map((j) => j.name);

  return (
    <section
      aria-label="11-judge breakdown"
      data-testid="judge-panel-breakdown"
      className="space-y-4"
    >
      {panelPartial && totalDossier > 0 && (
        <div
          data-testid="judge-panel-partial-banner"
          className="rounded-md border border-amber-500/40 bg-amber-500/[0.06] p-3 text-xs"
        >
          <p className="font-mono text-[10px] uppercase tracking-wider text-amber-300">
            Partial · {completedCount}/{totalDossier} judges completed
          </p>
          <p className="mt-1 text-foreground/85">
            {partialCount} judge(s) returned
            <span className="font-mono"> INSUFFICIENT_DATA</span> because the
            panel budget was exceeded. Pending judges are excluded from the
            verdict aggregation.
          </p>
          {pendingNames.length > 0 && (
            <p className="mt-1 font-mono text-[10px] text-amber-300/80">
              pending judges · {pendingNames.join(", ")}
            </p>
          )}
        </div>
      )}
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">11-Judge Panel · Breakdown</h3>
          <p className="text-[11px] text-muted-foreground">
            3 translation judges (BLEU / COMET / MQM) + 8 style judges (D1–D8).
            Run in parallel via <span className="font-mono">asyncio.gather</span>.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {overall !== null && (
            <Badge variant="outline" className="font-mono text-[10px]">
              overall · {overall.toFixed(2)}
            </Badge>
          )}
          {verdict && (
            <Badge
              variant={isFail ? "destructive" : "success"}
              className="font-mono text-[10px] uppercase tracking-wider"
            >
              verdict · {verdict}
            </Badge>
          )}
        </div>
      </header>

      {/* Translation judges (3 side-by-side) */}
      <div>
        <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          Translation judges
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {translationCells.map((cell) => {
            const dossier = dossierByName.get(cell.key.toLowerCase());
            const isPartial = Boolean(dossier?.panelBudgetExceeded);
            const isSoftSkip = Boolean(dossier?.softSkip) && !isPartial;
            const isDecider = isFail && cell.status === "fail" && !isPartial;
            return (
              <Card
                key={cell.key}
                data-testid={`judge-cell-${cell.key.toLowerCase()}`}
                className={cn(
                  "border-border/60",
                  isDecider && "border-amber-400/60 ring-1 ring-amber-400/30",
                  isPartial && "border-amber-500/40",
                )}
              >
                <CardContent className="p-3">
                  <div className="flex items-center justify-between gap-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[11px] font-semibold text-foreground">
                        {cell.key}
                      </span>
                      <MetricInfo metric={cell.key} />
                    </div>
                    {isPartial ? (
                      <MinusCircle
                        className="h-3.5 w-3.5 text-amber-300"
                        aria-label={`${cell.key} insufficient data`}
                      />
                    ) : cell.status === "pass" ? (
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" aria-label="passed" />
                    ) : cell.status === "fail" ? (
                      <XCircle className="h-3.5 w-3.5 text-destructive" aria-label="failed" />
                    ) : (
                      <MinusCircle className="h-3.5 w-3.5 text-muted-foreground" aria-label="not available" />
                    )}
                  </div>
                  <p
                    className={cn(
                      "mt-1 font-mono text-lg leading-none",
                      isPartial
                        ? "text-amber-300"
                        : cell.status === "pass"
                          ? "text-emerald-300"
                          : cell.status === "fail"
                            ? "text-destructive"
                            : "text-muted-foreground",
                    )}
                  >
                    {cell.display}
                  </p>
                  {isPartial ? (
                    <Badge
                      data-testid={`judge-cell-${cell.key.toLowerCase()}-partial`}
                      className="mt-1 border-amber-500/40 bg-amber-500/10 font-mono text-[9px] uppercase tracking-wider text-amber-300"
                    >
                      INSUFFICIENT_DATA · partial
                    </Badge>
                  ) : isSoftSkip ? (
                    <Badge className="mt-1 border-cyan-500/40 bg-cyan-500/10 font-mono text-[9px] uppercase tracking-wider text-cyan-300">
                      soft-skip
                    </Badge>
                  ) : (
                    <p className="mt-1 font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                      {cell.status === "na" ? "no reference data" : cell.status}
                    </p>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>

      {/* Style judges (8-cell grid, 4 cols on sm+) */}
      <div>
        <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          Style alignment judges
        </p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {styleCells.map((cell) => {
            const passed = cell.passed === true;
            const failed = cell.passed === false;
            const isDecider = isFail && failed;
            return (
              <Card
                key={cell.key}
                className={cn(
                  "border-border/60",
                  isDecider && "border-amber-400/60 ring-1 ring-amber-400/30",
                  cell.isHardGate && "bg-muted/10",
                )}
              >
                <CardContent className="space-y-1 p-2.5">
                  <div className="flex items-center justify-between gap-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[11px] font-semibold text-foreground">
                        {cell.key}
                      </span>
                      <MetricInfo metric={cell.key} />
                    </div>
                    {passed && (
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" aria-label={`${cell.key} passed`} />
                    )}
                    {failed && (
                      <XCircle className="h-3.5 w-3.5 text-destructive" aria-label={`${cell.key} failed`} />
                    )}
                    {cell.passed === undefined && (
                      <MinusCircle className="h-3.5 w-3.5 text-muted-foreground" aria-label={`${cell.key} pending`} />
                    )}
                  </div>
                  <p
                    className={cn(
                      "font-mono text-[9px] uppercase tracking-wider",
                      passed
                        ? "text-emerald-300"
                        : failed
                          ? "text-destructive"
                          : "text-muted-foreground",
                    )}
                  >
                    {passed ? "pass" : failed ? "fail" : "pending"}
                    {cell.isHardGate && (
                      <span className="ml-1 text-amber-300/80">· hard gate</span>
                    )}
                  </p>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>

      {/* W9-A: On-chain JudgePanel attestation (γ-strategy aggregate). */}
      {event.judgesAttestation && event.judgesAttestation.txHash && (
        <div
          data-testid="judge-panel-attestation"
          className="rounded-md border border-cyan-500/30 bg-cyan-500/[0.04] p-3 text-xs"
        >
          <p className="font-mono text-[10px] uppercase tracking-wider text-cyan-300">
            on-chain attestation · JudgePanel.recordAttestation
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
            <TxLink
              txHash={event.judgesAttestation.txHash}
              mode="live"
              label="attestation tx"
            />
            {event.judgesAttestation.attestationHash && (
              <span
                className="font-mono text-[10px] text-muted-foreground"
                title={event.judgesAttestation.attestationHash}
              >
                hash · {shortAddr(event.judgesAttestation.attestationHash, 10, 6)}
              </span>
            )}
            {typeof event.judgesAttestation.scoreScaled === "number" && (
              <span className="font-mono text-[10px] text-muted-foreground">
                score · {event.judgesAttestation.scoreScaled} / 1000
              </span>
            )}
          </div>
          <p className="mt-1 text-foreground/80">
            keccak256 of the 11-judge dossier JSON is anchored on Arc; the
            full dossier above can be re-hashed to verify nothing has been
            tampered with off-chain.
          </p>
        </div>
      )}

      {/* Decision summary */}
      <div
        className={cn(
          "rounded-md border p-3 text-xs",
          isFail
            ? "border-destructive/40 bg-destructive/[0.04]"
            : "border-emerald-500/30 bg-emerald-500/[0.04]",
        )}
      >
        <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          Why this verdict?
        </p>
        {isFail ? (
          <p className="mt-1 text-foreground/90">
            {styleHasFailures ? (
              <>
                One or more style judges returned <strong>FAIL</strong>. Hard
                gates (D5 Resolution Clarity, D8 Duplicate Detection) are
                blocking — any other failure rolls the overall verdict to
                <strong> FAIL</strong>.
              </>
            ) : (
              <>
                Translation quality fell below threshold (MQM &lt; 70 or
                COMET &lt; 0.7), or a major MQM error was flagged.
              </>
            )}
          </p>
        ) : (
          <p className="mt-1 text-foreground/90">
            All hard gates (D5, D8) passed and the overall quality score
            cleared the <span className="font-mono">PASS</span> threshold.
          </p>
        )}
      </div>
    </section>
  );
}
