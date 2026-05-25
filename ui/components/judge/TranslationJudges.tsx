import type { JudgeScore } from "@/lib/api";
import { cn } from "@/lib/utils";

const THRESHOLDS: Record<string, number> = {
  BLEU: 0.45,
  COMET: 0.7,
  MQM: 0.7,
};

function thresholdFor(name: string): number {
  // Match the judge label loosely so backend can return "bleu" / "BLEU score".
  const upper = name.toUpperCase();
  for (const key of Object.keys(THRESHOLDS)) {
    if (upper.includes(key)) return THRESHOLDS[key];
  }
  return 0.7;
}

export function TranslationJudges({ judges }: { judges: JudgeScore[] }) {
  const trio = judges.filter((j) => j.category === "translation");
  if (!trio.length) return null;
  return (
    <div>
      <div className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
        Translation quality (BLEU / COMET / MQM)
      </div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        {trio.map((j) => {
          const threshold = j.threshold ?? thresholdFor(j.judge);
          const passed = j.passed ?? j.score >= threshold;
          const pct = Math.min(100, Math.max(0, j.score * 100));
          const thresholdPct = Math.min(100, Math.max(0, threshold * 100));
          return (
            <div
              key={j.judge}
              className={cn(
                "rounded-md border bg-secondary/30 p-3",
                passed ? "border-emerald-500/30" : "border-amber-500/30",
              )}
            >
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  {j.judge}
                </span>
                <span
                  className={cn(
                    "font-mono text-lg",
                    j.score >= 0.85
                      ? "text-emerald-400"
                      : j.score >= 0.7
                        ? "text-primary"
                        : j.score > 0
                          ? "text-amber-400"
                          : "text-muted-foreground/50",
                  )}
                >
                  {j.score > 0 ? j.score.toFixed(2) : "—"}
                </span>
              </div>
              <div className="relative mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted/40">
                <div
                  className={cn(
                    "absolute inset-y-0 left-0 rounded-full transition-all duration-500",
                    passed ? "bg-emerald-400" : "bg-amber-400",
                  )}
                  style={{ width: `${pct}%` }}
                />
                <div
                  className="absolute inset-y-0 w-0.5 bg-fuchsia-400"
                  style={{ left: `${thresholdPct}%` }}
                  aria-hidden
                  title={`threshold ${threshold.toFixed(2)}`}
                />
              </div>
              <div className="mt-1 flex items-baseline justify-between text-[9px] text-muted-foreground">
                <span>threshold {threshold.toFixed(2)}</span>
                <span className={passed ? "text-emerald-400" : "text-amber-400"}>
                  {passed ? "PASS" : "BELOW"}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
