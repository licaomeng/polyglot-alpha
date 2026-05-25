"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { LeaderboardEntry } from "@/lib/api";
import { cn, formatUsd, shortAddr } from "@/lib/utils";

type SortKey = "rank" | "reputation" | "revenueUsd" | "winRate";

function ariaSortFor(active: boolean, dir: "asc" | "desc"): "ascending" | "descending" | "none" {
  if (!active) return "none";
  return dir === "asc" ? "ascending" : "descending";
}

export function LeaderboardTable({ entries }: { entries: LeaderboardEntry[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const maxRevenue = useMemo(
    () => Math.max(1, ...entries.map((e) => e.revenueUsd)),
    [entries],
  );

  const sorted = useMemo(() => {
    const copy = [...entries];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [entries, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // Numeric metrics: desc by default (highest first).
      setSortDir(key === "rank" ? "asc" : "desc");
    }
  };

  return (
    <Table aria-label="Agent leaderboard">
      <THead>
        <TR>
          <TH
            className="w-12"
            aria-sort={ariaSortFor(sortKey === "rank", sortDir)}
          >
            <SortButton
              label="#"
              active={sortKey === "rank"}
              dir={sortDir}
              onClick={() => toggleSort("rank")}
            />
          </TH>
          <TH>Agent</TH>
          <TH
            className="text-right"
            aria-sort={ariaSortFor(sortKey === "reputation", sortDir)}
          >
            <SortButton
              label="Rep."
              align="right"
              active={sortKey === "reputation"}
              dir={sortDir}
              onClick={() => toggleSort("reputation")}
            />
          </TH>
          <TH
            className="text-right"
            aria-sort={ariaSortFor(sortKey === "revenueUsd", sortDir)}
          >
            <SortButton
              label="Revenue"
              align="right"
              active={sortKey === "revenueUsd"}
              dir={sortDir}
              onClick={() => toggleSort("revenueUsd")}
            />
          </TH>
          <TH
            className="text-right"
            aria-sort={ariaSortFor(sortKey === "winRate", sortDir)}
          >
            <SortButton
              label="Win rate"
              align="right"
              active={sortKey === "winRate"}
              dir={sortDir}
              onClick={() => toggleSort("winRate")}
            />
          </TH>
        </TR>
      </THead>
      <TBody>
        {sorted.map((row) => {
          const pct = (row.revenueUsd / maxRevenue) * 100;
          return (
            <TR key={row.address}>
              <TD className="font-mono text-xs text-muted-foreground">#{row.rank}</TD>
              <TD>
                <Link
                  href={`/agents/${row.address}`}
                  className="group flex items-center gap-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded px-1 -mx-1"
                  aria-label={`View profile for ${row.alias ?? row.address}`}
                >
                  <span className="font-medium group-hover:text-primary">{row.alias ?? "—"}</span>
                  <span className="font-mono text-[10px] text-muted-foreground group-hover:text-primary">
                    {shortAddr(row.address)}
                  </span>
                </Link>
              </TD>
              <TD className="text-right font-mono text-xs">{row.reputation.toFixed(2)}</TD>
              <TD className="text-right font-mono text-xs">
                <div className="flex items-center justify-end gap-2">
                  <div
                    className="hidden h-1.5 w-20 overflow-hidden rounded-full bg-muted/40 sm:block"
                    aria-hidden
                  >
                    <div
                      className="h-full rounded-full bg-primary/70"
                      style={{ width: `${pct.toFixed(1)}%` }}
                    />
                  </div>
                  <span>{formatUsd(row.revenueUsd)}</span>
                </div>
              </TD>
              <TD className="text-right font-mono text-xs">
                {(row.winRate * 100).toFixed(0)}%
              </TD>
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}

function SortButton({
  label,
  active,
  dir,
  onClick,
  align = "left",
}: {
  label: string;
  active: boolean;
  dir: "asc" | "desc";
  onClick: () => void;
  align?: "left" | "right";
}) {
  const Icon = !active ? ArrowUpDown : dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded transition-colors hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        align === "right" && "flex-row-reverse",
        active && "text-foreground",
      )}
      aria-label={`Sort by ${label} ${active && dir === "asc" ? "descending" : "ascending"}`}
    >
      {label}
      <Icon className="h-3 w-3" aria-hidden />
    </button>
  );
}
