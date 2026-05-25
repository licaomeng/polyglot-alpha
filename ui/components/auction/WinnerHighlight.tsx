"use client";

import { motion } from "framer-motion";
import { Trophy } from "lucide-react";

export function WinnerHighlight() {
  return (
    <motion.span
      initial={{ scale: 0.6, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", stiffness: 220, damping: 14 }}
      className="inline-flex items-center gap-1 rounded-md bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-400"
      aria-label="Auction winner"
    >
      <Trophy className="h-3 w-3" aria-hidden />
      Winner
    </motion.span>
  );
}
