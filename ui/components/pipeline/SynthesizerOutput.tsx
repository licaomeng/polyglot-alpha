"use client";

import { motion } from "framer-motion";
import { Sparkles } from "lucide-react";

export function SynthesizerOutput({ synthesized }: { synthesized: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.15 }}
      className="rounded-md border border-primary/30 bg-primary/5 p-3"
    >
      <div className="mb-1 flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider text-primary">
        <Sparkles className="h-3 w-3" aria-hidden /> Synthesized output
      </div>
      <p className="text-xs leading-relaxed">{synthesized}</p>
    </motion.div>
  );
}
