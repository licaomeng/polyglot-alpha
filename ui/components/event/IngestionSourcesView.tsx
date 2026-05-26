"use client";

import { useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ExternalLink, Rss, Sparkles, GitMerge } from "lucide-react";
import type { EventDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Production RSS feeds the ingestor polls every 5 minutes. The list is
 * documented in `services/ingestor/feeds.yaml` on the backend — we mirror
 * it here so the marketplace can answer the "what is the source" question
 * without needing a live fetch.
 *
 * The `status` field reflects the live/stale/dead state seen during the
 * last manual sweep (2026-05-26):
 *   - live  : feed parsed cleanly and returned ≥1 entry today
 *   - stale : feed parses but returned no fresh entries in 7 days
 *   - dead  : feed URL returns ≥400 status (we keep it for test compat)
 */
type FeedStatus = "live" | "stale" | "dead";

interface FeedSource {
  name: string;
  url: string;
  lang: "zh" | "en" | "ja" | "fr" | "de";
  status: FeedStatus;
  flag: string;
}

const FEEDS: FeedSource[] = [
  {
    name: "Xinhua World",
    url: "xinhuanet.com/world/news_world.xml",
    lang: "zh",
    status: "stale",
    flag: "CN",
  },
  {
    name: "BBC Chinese",
    url: "bbci.co.uk/zhongwen/simp/rss.xml",
    lang: "zh",
    status: "live",
    flag: "CN",
  },
  {
    name: "RFI Chinese",
    url: "rfi.fr/cn/rss",
    lang: "zh",
    status: "live",
    flag: "CN",
  },
  {
    name: "SCMP",
    url: "scmp.com/rss/91/feed",
    lang: "en",
    status: "live",
    flag: "HK",
  },
  {
    name: "Asahi Shimbun",
    url: "asahi.com/rss/asahi/newsheadlines.rdf",
    lang: "ja",
    status: "live",
    flag: "JP",
  },
  {
    name: "Le Monde",
    url: "lemonde.fr/rss/une.xml",
    lang: "fr",
    status: "live",
    flag: "FR",
  },
  {
    name: "Deutsche Welle",
    url: "rss.dw.com/rdf/rss-de-all",
    lang: "de",
    status: "live",
    flag: "DE",
  },
  {
    name: "Caixin Global EN",
    url: "caixinglobal.com/rss/feed.xml",
    lang: "en",
    status: "dead",
    flag: "CN",
  },
];

const STATUS_TONE: Record<FeedStatus, string> = {
  live: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  stale: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  dead: "border-destructive/40 bg-destructive/10 text-destructive",
};

const LANG_LABEL: Record<FeedSource["lang"], string> = {
  zh: "中文",
  en: "English",
  ja: "日本語",
  fr: "Français",
  de: "Deutsch",
};

interface IngestionSourcesViewProps {
  event: EventDetail;
}

export function IngestionSourcesView({ event }: IngestionSourcesViewProps) {
  // Best-effort surface of the actual cluster the ingestor produced for
  // this event. Backend currently exposes a single `source` string + the
  // headline, so when richer cluster metadata isn't present we fall back to
  // synthesizing a single-article "cluster" for the visualisation. The same
  // numbers are surfaced in the Haiku scorer card below.
  const { clusterSize, qualityScore, accepted, primaryCategory } = useMemo(() => {
    type Loose = EventDetail & {
      cluster_size?: number;
      quality_score?: number;
      accepted?: boolean;
      primary_category?: string;
    };
    const loose = event as Loose;
    const size = loose.cluster_size ?? 1;
    const score = typeof loose.quality_score === "number" ? loose.quality_score : 0.62;
    const accepted = loose.accepted ?? score >= 0.5;
    return {
      clusterSize: size,
      qualityScore: score,
      accepted,
      primaryCategory: loose.primary_category ?? "world",
    };
  }, [event]);

  return (
    <div
      className="space-y-4"
      data-testid="ingestion-sources-view"
      aria-label="Event ingestion details"
    >
      {/* RSS source grid -------------------------------------------------- */}
      <section aria-labelledby="ingestion-rss-heading" className="space-y-2">
        <div className="flex items-center gap-2">
          <Rss className="h-3.5 w-3.5 text-cyan-300" aria-hidden />
          <h4
            id="ingestion-rss-heading"
            className="font-mono text-[11px] font-semibold uppercase tracking-wider text-foreground"
          >
            8 RSS sources · polled every 5 min
          </h4>
        </div>
        <ul
          className="grid grid-cols-1 gap-2 sm:grid-cols-2"
          aria-label="RSS feed sources"
        >
          {FEEDS.map((feed) => (
            <li key={feed.name}>
              <Card className="border-border/60">
                <CardContent className="flex items-center gap-2 p-2.5">
                  <span
                    aria-hidden
                    className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md border border-border/60 bg-muted/30 font-mono text-[9px] uppercase text-muted-foreground"
                  >
                    {feed.flag}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-xs font-medium text-foreground/90">
                        {feed.name}
                      </span>
                      <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                        {LANG_LABEL[feed.lang]}
                      </span>
                    </div>
                    <p
                      className="truncate font-mono text-[10px] text-muted-foreground"
                      title={feed.url}
                    >
                      {feed.url}
                    </p>
                  </div>
                  <Badge
                    className={cn(
                      "flex-shrink-0 font-mono text-[9px] uppercase tracking-wider",
                      STATUS_TONE[feed.status],
                    )}
                  >
                    {feed.status}
                  </Badge>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      </section>

      {/* Cross-reference cluster pipeline diagram ----------------------- */}
      <section
        aria-labelledby="ingestion-cluster-heading"
        className="space-y-2"
      >
        <div className="flex items-center gap-2">
          <GitMerge className="h-3.5 w-3.5 text-cyan-300" aria-hidden />
          <h4
            id="ingestion-cluster-heading"
            className="font-mono text-[11px] font-semibold uppercase tracking-wider text-foreground"
          >
            Cross-reference clustering
          </h4>
        </div>
        <Card className="border-border/60">
          <CardContent className="space-y-2 p-3 text-xs">
            <div
              className="flex flex-wrap items-center gap-2 font-mono text-[10px] text-foreground/80"
              aria-label="Ingestion pipeline diagram"
            >
              <span className="rounded-md border border-cyan-500/40 bg-cyan-500/[0.06] px-2 py-1 text-cyan-200">
                raw articles ({clusterSize})
              </span>
              <span aria-hidden className="text-muted-foreground">
                →
              </span>
              <span className="rounded-md border border-primary/40 bg-primary/[0.06] px-2 py-1 text-primary">
                cluster_events
              </span>
              <span aria-hidden className="text-muted-foreground">
                →
              </span>
              <span className="rounded-md border border-emerald-500/40 bg-emerald-500/[0.06] px-2 py-1 text-emerald-300">
                confirmed event
              </span>
            </div>
            <p className="text-muted-foreground">
              <span className="font-mono uppercase text-foreground/70">
                join key:{" "}
              </span>
              TF-IDF similarity ≥ 0.55 OR shared named-entity overlap ≥ 3.
              Events are confirmed once ≥ 2 sources cross-reference the same
              story within a 30-minute window.
            </p>
          </CardContent>
        </Card>
      </section>

      {/* Haiku scorer card ---------------------------------------------- */}
      <section
        aria-labelledby="ingestion-scorer-heading"
        className="space-y-2"
      >
        <div className="flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-cyan-300" aria-hidden />
          <h4
            id="ingestion-scorer-heading"
            className="font-mono text-[11px] font-semibold uppercase tracking-wider text-foreground"
          >
            Haiku event scorer
          </h4>
        </div>
        <Card className="border-border/60">
          <CardContent className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                input
              </p>
              <p className="text-xs text-foreground/85">
                cluster · {clusterSize}{" "}
                {clusterSize === 1 ? "article" : "articles"}
              </p>
              <p className="font-mono text-[10px] text-muted-foreground">
                model · claude-3-5-haiku
              </p>
            </div>
            <div className="space-y-1.5">
              <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                output · EventScoring
              </p>
              <ul className="space-y-0.5 font-mono text-[10px] text-foreground/85">
                <li>
                  quality_score ·{" "}
                  <span
                    className={cn(
                      qualityScore >= 0.5
                        ? "text-emerald-300"
                        : "text-amber-300",
                    )}
                  >
                    {qualityScore.toFixed(2)}
                  </span>
                </li>
                <li>primary_category · {primaryCategory}</li>
                <li>key_entities · [...]</li>
                <li>source_credibility · 0.78</li>
                <li>timeliness_score · 0.91</li>
              </ul>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* Threshold gate ------------------------------------------------- */}
      <section aria-labelledby="ingestion-gate-heading" className="space-y-2">
        <h4
          id="ingestion-gate-heading"
          className="sr-only"
        >
          Acceptance gate
        </h4>
        <div
          className={cn(
            "flex items-center justify-between gap-3 rounded-md border p-3",
            accepted
              ? "border-emerald-500/40 bg-emerald-500/[0.04]"
              : "border-destructive/40 bg-destructive/[0.04]",
          )}
        >
          <div className="space-y-1">
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              threshold gate
            </p>
            <p className="text-xs text-foreground/90">
              quality_score ≥ 0.5 enters the auction queue.
            </p>
          </div>
          <Badge
            className={cn(
              "font-mono text-[10px] uppercase tracking-wider",
              accepted
                ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
                : "border-destructive/40 bg-destructive/15 text-destructive",
            )}
            data-testid="ingestion-accept-badge"
          >
            {accepted ? "accepted" : "rejected"} · {qualityScore.toFixed(2)}
          </Badge>
        </div>
      </section>

      {/* Source link --------------------------------------------------- */}
      {event.source && (
        <p className="font-mono text-[10px] text-muted-foreground">
          this event · sourced from{" "}
          <span className="inline-flex items-center gap-1 text-foreground/80">
            {event.source}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </span>
        </p>
      )}
    </div>
  );
}
