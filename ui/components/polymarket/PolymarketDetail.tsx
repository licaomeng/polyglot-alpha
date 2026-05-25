"use client";

import { useState } from "react";
import { ExternalLink, ChevronDown, AlertTriangle, Loader2 } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { BuilderCodeBadge } from "./BuilderCodeBadge";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { submitPolymarketReal, type EventDetail } from "@/lib/api";

type PM = NonNullable<EventDetail["polymarket"]>;

interface Props {
  polymarket: PM;
  eventId: string;
}

function deriveMode(pm: PM): "live" | "dry_run" | "mock" {
  if (pm.mode) return pm.mode;
  if (pm.isSimulated === false) return "live";
  if (pm.isSimulated === true) return "dry_run";
  return "mock";
}

function ModeBadge({ mode }: { mode: "live" | "dry_run" | "mock" }) {
  if (mode === "live") {
    return (
      <Badge variant="success" className="gap-1.5" aria-label="Live submission to Polymarket prod">
        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
        LIVE
      </Badge>
    );
  }
  if (mode === "dry_run") {
    return (
      <Badge variant="warning" className="gap-1.5" aria-label="Dry-run submission (not on prod)">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />
        DRY_RUN
      </Badge>
    );
  }
  return (
    <Badge variant="destructive" className="gap-1.5" aria-label="Mock data">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-rose-400" />
      MOCK
    </Badge>
  );
}

export function PolymarketDetail({ polymarket, eventId }: Props) {
  const mode = deriveMode(polymarket);
  const [showPayload, setShowPayload] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmitReal = async () => {
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const r = await submitPolymarketReal(eventId);
      setResult(r.market_url ?? r.market_id ?? "Submitted");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Submission failed";
      setError(msg);
    } finally {
      setSubmitting(false);
      setConfirming(false);
    }
  };

  return (
    <div className="space-y-3 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <ModeBadge mode={mode} />
        <BuilderCodeBadge code={polymarket.builderCode} />
        {polymarket.status && (
          <span className="rounded-md border border-border/60 bg-card/40 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            status · {polymarket.status}
          </span>
        )}
      </div>

      {polymarket.marketUrl ? (
        <a
          href={polymarket.marketUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex items-center gap-1 rounded font-mono text-primary hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {polymarket.marketUrl}
          <ExternalLink className="h-3 w-3" aria-hidden />
        </a>
      ) : (
        polymarket.marketId && (
          <span className="font-mono text-muted-foreground">market_id · {polymarket.marketId}</span>
        )
      )}

      {polymarket.submissionTx && (
        <p className="font-mono text-muted-foreground">submission_tx · {polymarket.submissionTx}</p>
      )}

      {mode === "dry_run" && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-400" aria-hidden />
            <div className="min-w-0 space-y-2">
              <p className="text-amber-200/90">
                This submission was simulated (dry-run). Promote it to Polymarket production?
              </p>
              {!confirming ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setConfirming(true)}
                  disabled={submitting}
                >
                  Submit Real
                </Button>
              ) : (
                <div className="space-y-2 rounded-md border border-destructive/40 bg-destructive/5 p-2.5">
                  <p className="text-destructive">
                    This will POST to Polymarket prod review queue. Continue?
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={handleSubmitReal}
                      disabled={submitting}
                    >
                      {submitting ? (
                        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                      ) : null}
                      {submitting ? "Submitting…" : "Confirm — Submit Real"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setConfirming(false)}
                      disabled={submitting}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              )}
              {result && (
                <p className="font-mono text-[10px] text-emerald-400">
                  Submitted → {result}
                </p>
              )}
              {error && (
                <p className="font-mono text-[10px] text-destructive">{error}</p>
              )}
            </div>
          </div>
        </div>
      )}

      {polymarket.payload && (
        <div className="rounded-md border border-border/40 bg-card/40">
          <button
            type="button"
            onClick={() => setShowPayload((s) => !s)}
            className="flex w-full items-center justify-between px-3 py-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-expanded={showPayload}
          >
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              View API Payload
            </span>
            <ChevronDown
              className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", showPayload && "rotate-180")}
              aria-hidden
            />
          </button>
          <AnimatePresence initial={false}>
            {showPayload && (
              <motion.pre
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="overflow-x-auto rounded-b-md border-t border-border/40 bg-background/40 p-3 font-mono text-[10px] leading-relaxed text-foreground/80"
              >
                {JSON.stringify(polymarket.payload, null, 2)}
              </motion.pre>
            )}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
