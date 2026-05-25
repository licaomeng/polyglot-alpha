import type { JudgeScore } from "@/lib/api";
import { TranslationJudges } from "./TranslationJudges";
import { StyleAlignmentJudges } from "./StyleAlignmentJudges";
import { ClosedIPCallout } from "./ClosedIPCallout";
import { cn } from "@/lib/utils";
import { CheckCircle2, XCircle } from "lucide-react";

interface Props {
  judges: JudgeScore[];
  verdict?: string;
  reasoning?: string;
}

export function JudgePanel({ judges, verdict, reasoning }: Props) {
  if (!judges?.length) return null;
  const isPass = (verdict ?? "").toUpperCase() === "PASS";
  return (
    <div className="space-y-4">
      <TranslationJudges judges={judges} />
      <StyleAlignmentJudges judges={judges} />
      <ClosedIPCallout />
      {verdict && (
        <div
          className={cn(
            "flex items-start gap-2 rounded-md border p-3 text-xs",
            isPass
              ? "border-emerald-500/40 bg-emerald-500/5"
              : "border-destructive/40 bg-destructive/5",
          )}
        >
          {isPass ? (
            <CheckCircle2 className="h-4 w-4 flex-shrink-0 text-emerald-400" aria-hidden />
          ) : (
            <XCircle className="h-4 w-4 flex-shrink-0 text-destructive" aria-hidden />
          )}
          <div className="min-w-0">
            <div
              className={cn(
                "font-mono text-[10px] uppercase tracking-wider",
                isPass ? "text-emerald-300" : "text-destructive",
              )}
            >
              Verdict · {verdict}
            </div>
            {reasoning && <p className="mt-1 text-foreground/85">{reasoning}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
