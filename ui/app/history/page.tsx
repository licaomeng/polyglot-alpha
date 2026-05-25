"use client";

import { useMemo, useState } from "react";
import { useEventList } from "@/hooks/useEventList";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { RealVsMockBadge } from "@/components/shared/RealVsMockBadge";
import { EmptyState } from "@/components/shared/EmptyState";
import Link from "next/link";
import { Download, Search } from "lucide-react";
import { relativeTime } from "@/lib/utils";

export default function HistoryPage() {
  const { data, isLoading } = useEventList();
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const rows = useMemo(() => {
    if (!data) return [];
    return data
      .filter((e) => (statusFilter === "all" ? true : e.status === statusFilter))
      .filter((e) =>
        query.trim().length === 0
          ? true
          : e.headline.toLowerCase().includes(query.toLowerCase()) ||
            e.source.toLowerCase().includes(query.toLowerCase()) ||
            (e.marketSymbol ?? "").toLowerCase().includes(query.toLowerCase()),
      );
  }, [data, query, statusFilter]);

  const exportCsv = () => {
    const headers = ["id", "headline", "source", "status", "mode", "marketSymbol", "ingestedAt"];
    const csv = [
      headers.join(","),
      ...rows.map((r) =>
        [r.id, JSON.stringify(r.headline), r.source, r.status, r.mode, r.marketSymbol ?? "", r.ingestedAt].join(","),
      ),
    ].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `polyglot-history-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="container space-y-6 py-8">
      <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">History</h1>
          <p className="text-xs text-muted-foreground">
            Searchable archive of every event the pipeline has processed.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              className="h-9 w-64 rounded-md border border-input bg-background pl-7 pr-3 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
              placeholder="Search headline, source, symbol…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search history"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-2 text-xs"
            aria-label="Filter by status"
          >
            <option value="all">All status</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="pending">Pending</option>
            <option value="failed">Failed</option>
          </select>
          <Button variant="outline" size="sm" onClick={exportCsv} disabled={!rows.length}>
            <Download className="h-3.5 w-3.5" aria-hidden /> CSV
          </Button>
        </div>
      </header>

      {isLoading && <Skeleton className="h-72" />}
      {!isLoading && rows.length === 0 && <EmptyState title="No events match" />}
      {!isLoading && rows.length > 0 && (
        <div className="rounded-xl border border-border/60 bg-card/40">
          <Table aria-label="Event history">
            <THead>
              <TR>
                <TH>Headline</TH>
                <TH>Source</TH>
                <TH>Status</TH>
                <TH>Mode</TH>
                <TH>Symbol</TH>
                <TH className="text-right">Ingested</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((r) => (
                <TR key={r.id}>
                  <TD>
                    <Link href={`/events/${r.id}`} className="hover:text-primary">
                      {r.headline}
                    </Link>
                  </TD>
                  <TD className="text-xs text-muted-foreground">{r.source}</TD>
                  <TD>
                    <Badge variant="secondary" className="capitalize">
                      {r.status}
                    </Badge>
                  </TD>
                  <TD>
                    <RealVsMockBadge mode={r.mode} />
                  </TD>
                  <TD className="font-mono text-[10px] text-muted-foreground">
                    {r.marketSymbol ?? "—"}
                  </TD>
                  <TD className="text-right font-mono text-xs">{relativeTime(r.ingestedAt)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}
    </div>
  );
}
