"use client";

import { Info } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * Canonical explanations for every metric/judge that surfaces in the
 * marketplace UI. Centralising the text here means the same definition is
 * used wherever a metric appears (judge panel, timeline, popovers, etc.).
 */
export const METRIC_DOCS: Record<
  string,
  { title: string; range: string; what: string; gate?: "HARD" | "SOFT" }
> = {
  BLEU: {
    title: "BLEU",
    range: "0–100 (higher = better)",
    what: "Standard n-gram match against a reference translation. Currently null when no reference data is supplied (the marketplace does not require one).",
  },
  COMET: {
    title: "COMET (Kiwi)",
    range: "0–1 (higher = better)",
    what: "Reference-free translation quality from the wmt22-cometkiwi-da model. The marketplace relies on this when no human reference exists.",
  },
  MQM: {
    title: "MQM (Multidimensional Quality Metric)",
    range: "0–100 (higher = better, but major errors hurt heavily)",
    what: "LLM-graded error taxonomy (Accuracy / Fluency / Terminology / Style). Each MAJOR error subtracts 5pts; each MINOR subtracts 1pt.",
  },
  D1: {
    title: "D1 · Structural",
    range: "PASS / FAIL",
    what: "Does the question fit a binary Polymarket template (yes/no resolvable)?",
  },
  D2: {
    title: "D2 · Stylistic",
    range: "PASS / FAIL",
    what: "LLM-graded match to the Polymarket corpus stylistic conventions (concise, declarative, lower-case operator-style).",
  },
  D3: {
    title: "D3 · Framing",
    range: "PASS / FAIL",
    what: "Is the question framed as a real prediction (an event in the world), not an opinion poll or a hypothetical?",
  },
  D4: {
    title: "D4 · Granularity",
    range: "PASS / FAIL",
    what: "Single resolvable question — no compound and/or, no nested clauses.",
  },
  D5: {
    title: "D5 · Resolution Clarity",
    range: "PASS / FAIL",
    what: "Clear resolution source + cutoff datetime that an arbiter can verify deterministically.",
    gate: "HARD",
  },
  D6: {
    title: "D6 · Source Reliability",
    range: "PASS / FAIL",
    what: "Authoritative source (regulator, central bank, official feed) backing the question.",
  },
  D7: {
    title: "D7 · Leading",
    range: "PASS / FAIL",
    what: "Does the wording leak the answer (e.g. 'Will Tesla finally…')? Pass = neutral phrasing.",
  },
  D8: {
    title: "D8 · Duplicate Detection",
    range: "PASS / FAIL",
    what: "FAISS kNN over ~75K historical Polymarket markets. Rejects near-duplicates (cosine ≥ 0.94).",
    gate: "HARD",
  },
};

/**
 * Inline `(i)` icon that opens a tooltip with the canonical metric doc.
 */
export function MetricInfo({
  metric,
  className,
}: {
  metric: keyof typeof METRIC_DOCS | string;
  className?: string;
}) {
  const key = metric.toUpperCase() as keyof typeof METRIC_DOCS;
  const doc = METRIC_DOCS[key];
  if (!doc) return null;
  return (
    <Tooltip
      content={
        <div className="space-y-1">
          <div className="flex items-center gap-1.5">
            <span className="font-mono text-[11px] font-semibold text-foreground">
              {doc.title}
            </span>
            {doc.gate === "HARD" && (
              <span className="rounded-sm bg-amber-500/20 px-1 py-0.5 text-[9px] font-mono uppercase tracking-wider text-amber-300">
                hard gate
              </span>
            )}
          </div>
          <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {doc.range}
          </p>
          <p className="text-foreground/85">{doc.what}</p>
        </div>
      }
    >
      <button
        type="button"
        aria-label={`Explain ${doc.title}`}
        className={cn(
          "inline-flex h-4 w-4 items-center justify-center rounded-full border border-border/50 text-muted-foreground transition-colors hover:border-primary/60 hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          className,
        )}
      >
        <Info className="h-2.5 w-2.5" aria-hidden />
      </button>
    </Tooltip>
  );
}

/**
 * Inline `(i)` icon that opens a free-form tooltip — used for per-phase
 * explanations on the Timeline (where the doc isn't a metric).
 */
export function PhaseInfo({
  title,
  what,
  signals,
  produces,
  className,
}: {
  title: string;
  what: string;
  signals?: string;
  produces?: string;
  className?: string;
}) {
  return (
    <Tooltip
      widthClassName="max-w-sm"
      content={
        <div className="space-y-1.5">
          <p className="font-mono text-[11px] font-semibold text-foreground">
            {title}
          </p>
          <p className="text-foreground/90">{what}</p>
          {signals && (
            <p className="text-muted-foreground">
              <span className="font-mono text-[10px] uppercase tracking-wider text-cyan-300/80">
                signal ·{" "}
              </span>
              {signals}
            </p>
          )}
          {produces && (
            <p className="text-muted-foreground">
              <span className="font-mono text-[10px] uppercase tracking-wider text-emerald-300/80">
                produces ·{" "}
              </span>
              {produces}
            </p>
          )}
        </div>
      }
    >
      <button
        type="button"
        aria-label={`Explain phase ${title}`}
        className={cn(
          "inline-flex h-4 w-4 items-center justify-center rounded-full border border-border/50 text-muted-foreground transition-colors hover:border-primary/60 hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          className,
        )}
      >
        <Info className="h-2.5 w-2.5" aria-hidden />
      </button>
    </Tooltip>
  );
}
