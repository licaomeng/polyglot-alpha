"use client";

import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { BidEntry } from "@/lib/api";
import { formatUsd, shortAddr } from "@/lib/utils";
import { WinnerHighlight } from "./WinnerHighlight";
import { motion } from "framer-motion";

export function BidTable({ bids }: { bids: BidEntry[] }) {
  const sorted = [...bids].sort((a, b) => b.bid - a.bid);
  return (
    <Table aria-label="USDC auction bids">
      <THead>
        <TR>
          <TH>Agent</TH>
          <TH className="text-right">Bid (USDC)</TH>
          <TH className="text-right">Rep.</TH>
          <TH />
        </TR>
      </THead>
      <TBody>
        {sorted.map((bid, idx) => (
          <motion.tr
            key={bid.agent}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: idx * 0.05 }}
            className={
              bid.winner
                ? "border-b border-emerald-500/20 bg-emerald-500/5"
                : "border-b border-border/40"
            }
          >
            <TD className="font-mono text-xs">{shortAddr(bid.agent)}</TD>
            <TD className="text-right font-mono text-xs">{formatUsd(bid.bid, 2)}</TD>
            <TD className="text-right font-mono text-xs">{bid.reputation.toFixed(2)}</TD>
            <TD className="w-20">{bid.winner && <WinnerHighlight />}</TD>
          </motion.tr>
        ))}
      </TBody>
    </Table>
  );
}
