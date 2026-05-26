"use client";

import { useMemo, useState } from "react";
import { useEventList } from "@/hooks/useEventList";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { EventStatusBadge } from "@/components/event/EventStatusBadge";
import { RealVsMockBadge } from "@/components/shared/RealVsMockBadge";
import { EmptyState } from "@/components/shared/EmptyState";
import Link from "next/link";
import { Download, Search } from "lucide-react";
import { relativeTime } from "@/lib/utils";
import {
  BUCKET_LABEL,
  STATUS_BUCKETS,
  bucketMatches,
  type StatusBucket,
} from "@/lib/status";

export default function HistoryPage() {
  const { data, isLoading } = useEventList();
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusBucket>("all");

  const rows = useMemo(() => {
    if (!data) return [];
    return data
      .filter((e) => bucketMatches(String(e.status), statusFilter))
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
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:flex-wrap">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              className="h-10 w-full rounded-md border border-input bg-background pl-7 pr-3 text-base sm:h-9 sm:w-64 sm:text-xs focus:outline-none focus:ring-2 focus:ring-ring"
              placeholder="Search headline, source, symbol…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search history"
            />
          </div>
          <div className="flex gap-2">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusBucket)}
              className="h-10 flex-1 rounded-md border border-input bg-background px-2 text-sm sm:h-9 sm:flex-none sm:text-xs"
              aria-label="Filter by status"
            >
              {STATUS_BUCKETS.map((b) => (
                <option key={b} value={b}>
                  {b === "all" ? "All status" : BUCKET_LABEL[b]}
                </option>
              ))}
            </select>
            <Button
              variant="outline"
              size="sm"
              onClick={exportCsv}
              disabled={!rows.length}
              className="min-h-[40px] sm:min-h-0"
            >
              <Download className="h-3.5 w-3.5" aria-hidden /> CSV
            </Button>
          </div>
        </div>
      </header>

      {isLoading && <Skeleton className="h-72" />}
      {!isLoading && rows.length === 0 && <EmptyState title="No events match" />}
      {!isLoading && rows.length > 0 && (
        <>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Showing {rows.length} of {data?.length ?? 0} events
            {statusFilter !== "all" && ` · filter: ${BUCKET_LABEL[statusFilter]}`}
          </p>
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
                    <EventStatusBadge status={r.status} />
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
        </>
      )}
    </div>
  );
}
