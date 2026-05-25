"use client";

import { motion } from "framer-motion";
import { MessageSquare, Check } from "lucide-react";

interface DebateEntry {
  analyst: string;
  argument: string;
  verdict?: string;
}

export function DebateTrace({ debate }: { debate: DebateEntry[] }) {
  if (!debate?.length) return null;
  return (
    <div className="space-y-1.5">
      <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
        Analyst debate
      </div>
      <ol className="space-y-1.5">
        {debate.map((entry, idx) => (
          <motion.li
            key={`${entry.analyst}-${idx}`}
            initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: idx * 0.08 }}
            className="flex gap-2 rounded-md border border-border/50 bg-card/40 p-2.5"
          >
            <MessageSquare className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-fuchsia-400" aria-hidden />
            <div className="min-w-0 text-xs">
              <div className="font-mono text-[10px] uppercase tracking-wider text-fuchsia-300">
                {entry.analyst}
              </div>
              <p className="mt-0.5 text-foreground/90">{entry.argument}</p>
              {entry.verdict && (
                <p className="mt-1 flex items-start gap-1 rounded bg-emerald-500/10 p-1.5 text-[11px] text-emerald-300">
                  <Check className="mt-0.5 h-3 w-3 flex-shrink-0" aria-hidden />
                  {entry.verdict}
                </p>
              )}
            </div>
          </motion.li>
        ))}
      </ol>
    </div>
  );
}
