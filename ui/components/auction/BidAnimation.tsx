"use client";

import { motion, AnimatePresence } from "framer-motion";
import type { BidEntry } from "@/lib/api";
import { shortAddr, formatUsd } from "@/lib/utils";

export function BidAnimation({ latest }: { latest?: BidEntry }) {
  return (
    <div className="relative h-12 overflow-hidden rounded-md border border-border/60 bg-card/40">
      <AnimatePresence mode="wait">
        {latest && (
          <motion.div
            key={`${latest.agent}-${latest.bid}`}
            initial={{ y: 24, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -24, opacity: 0 }}
            transition={{ duration: 0.3 }}
            className="absolute inset-0 flex items-center justify-between px-3 text-xs"
          >
            <span className="font-mono text-muted-foreground">{shortAddr(latest.agent)}</span>
            <span className="font-mono font-semibold text-primary">
              {formatUsd(latest.bid)}
            </span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
