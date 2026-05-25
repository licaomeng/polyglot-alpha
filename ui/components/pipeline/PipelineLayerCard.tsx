"use client";

import { motion } from "framer-motion";
import { DebateTrace } from "./DebateTrace";
import { SynthesizerOutput } from "./SynthesizerOutput";
import { Languages, GitMerge, Sparkles, Copy, Check } from "lucide-react";
import type { EventDetail } from "@/lib/api";
import { useState } from "react";
import { cn } from "@/lib/utils";

const LAYERS = [
  { key: "source", label: "L1 · Source Analysts", icon: Languages, defaultModel: "qwen-32b" },
  {
    key: "debate",
    label: "L2 · Bull-Bear Debate",
    icon: GitMerge,
    defaultModel: "claude-4.7-opus",
  },
  { key: "synthesize", label: "L3 · Synthesis", icon: Sparkles, defaultModel: "gemini-2.5-pro" },
  { key: "risk", label: "L4 · Risk Panel", icon: Sparkles, defaultModel: "deepseek-r1" },
  { key: "final", label: "L5 · Final JSON", icon: Sparkles, defaultModel: "claude-4.7-opus" },
];

type T = NonNullable<EventDetail["translation"]>;

function CopyButton({ text, ariaLabel }: { text: string; ariaLabel: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(text).catch(() => {});
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      aria-label={ariaLabel}
      className="ml-2 inline-flex items-center rounded p-1 text-muted-foreground hover:bg-accent/10 hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {copied ? <Check className="h-3 w-3" aria-hidden /> : <Copy className="h-3 w-3" aria-hidden />}
    </button>
  );
}

export function PipelineLayerCard({ translation }: { translation: T }) {
  // Backend may return enriched `layerDetails`; fall back to default model badges
  // when absent so the UI still renders structurally.
  const detailsByKey = new Map(
    (translation.layerDetails ?? []).map((d) => [d.layer, d]),
  );

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
        {LAYERS.map((layer, idx) => {
          const Icon = layer.icon;
          const d = detailsByKey.get(layer.key);
          const model = d?.model ?? layer.defaultModel;
          const duration = d?.durationMs;
          return (
            <motion.div
              key={layer.key}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: idx * 0.06 }}
              className="space-y-1.5 rounded-md border border-border/60 bg-secondary/20 p-2"
            >
              <div className="flex items-center justify-between">
                <Icon className="h-3.5 w-3.5 text-primary" aria-hidden />
                <span className="font-mono text-[9px] uppercase tracking-wider text-accent">
                  {model}
                </span>
              </div>
              <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                {layer.label}
              </div>
              {typeof duration === "number" && (
                <div className="font-mono text-[9px] text-muted-foreground">
                  {duration}ms
                </div>
              )}
              {d?.input && (
                <p
                  className="line-clamp-2 text-[10px] leading-snug text-muted-foreground"
                  title={d.input}
                >
                  in: {d.input}
                </p>
              )}
              {d?.output && (
                <p className="line-clamp-2 text-[10px] leading-snug text-foreground/80" title={d.output}>
                  out: {d.output}
                </p>
              )}
            </motion.div>
          );
        })}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
              Source · zh-CN
            </div>
            <CopyButton text={translation.source} ariaLabel="Copy source text" />
          </div>
          <p className="rounded-md border border-border/60 bg-secondary/20 p-3 text-sm leading-relaxed">
            {translation.source}
          </p>
        </div>
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
              Target · en-US
            </div>
            <CopyButton text={translation.target} ariaLabel="Copy translated text" />
          </div>
          <p className="rounded-md border border-primary/30 bg-primary/5 p-3 text-sm leading-relaxed">
            {translation.target}
          </p>
        </div>
      </div>

      {Array.isArray(translation.framings) && translation.framings.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
            K={translation.framings.length} framing variants
          </div>
          <ol className="space-y-1.5">
            {translation.framings.map((f, idx) => (
              <li
                key={idx}
                className={cn(
                  "flex items-start gap-2 rounded-md border border-border/40 bg-card/40 p-2 text-xs",
                  idx === 0 && "border-primary/40 bg-primary/5",
                )}
              >
                <span className="font-mono text-[10px] text-muted-foreground">k{idx + 1}</span>
                <span className="flex-1 text-foreground/90">{f}</span>
                <CopyButton text={f} ariaLabel={`Copy framing variant ${idx + 1}`} />
              </li>
            ))}
          </ol>
        </div>
      )}

      <DebateTrace debate={translation.debate} />
      <SynthesizerOutput synthesized={translation.synthesized} />
    </div>
  );
}
