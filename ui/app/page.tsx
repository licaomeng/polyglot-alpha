"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import dynamic from "next/dynamic";
import { EventCard } from "@/components/event/EventCard";
import { useEventList } from "@/hooks/useEventList";
import type { EventSummary, PhaseState } from "@/lib/api";
import { TriggerButton } from "@/components/TriggerButton";

const WorkflowOverview = dynamic(
  () => import("@/components/workflow/WorkflowOverview").then((m) => m.WorkflowOverview),
  {
    ssr: false,
    loading: () => (
      <div className="h-[420px] w-full animate-pulse rounded-xl border border-border/60 bg-muted/30 sm:h-[520px] md:h-[600px]" />
    ),
  },
);
import { ArrowRight, Workflow, ShieldCheck, Coins } from "lucide-react";

// Event list summaries may be hydrated with phase data when the backend
// returns the full EventDetail shape; treat the field as optional.
function getFeaturedPhases(
  event: EventSummary | undefined,
): PhaseState[] | undefined {
  if (!event) return undefined;
  const maybePhases = (event as Partial<{ phases: PhaseState[] }>).phases;
  return Array.isArray(maybePhases) ? maybePhases : undefined;
}

const THESIS = [
  {
    icon: Workflow,
    title: "Cross-language alpha",
    body: "Translate non-English market events into liquid English-priced contracts before the market does.",
  },
  {
    icon: ShieldCheck,
    title: "Verifiable pipeline",
    body: "Every phase — auction, debate, judgement, anchor — is cryptographically attestable.",
  },
  {
    icon: Coins,
    title: "Streaming builder fees",
    body: "Settled translations flow into Polymarket V2 via builder code and pay fees continuously.",
  },
];

export default function HomePage() {
  const { data: events } = useEventList();
  const featured = events?.[0];

  return (
    <div className="container space-y-12 py-10">
      <section className="grid-bg relative overflow-hidden rounded-2xl border border-border/60 px-6 py-12 md:py-20">
        <Badge variant="info" className="mb-4 inline-flex">
          v2 · cyber pricing engine
        </Badge>
        <h1 className="max-w-3xl text-4xl font-bold leading-tight tracking-tight text-balance md:text-5xl 3xl:max-w-5xl 3xl:text-6xl 4xl:max-w-7xl 4xl:text-7xl">
          Decentralized{" "}
          <span className="text-primary">cross-language alpha</span>, from headline to on-chain
          anchor in under{" "}
          <span className="font-mono text-accent">60s</span>.
        </h1>
        <p className="mt-4 max-w-2xl text-sm text-muted-foreground md:text-base 3xl:max-w-4xl 3xl:text-lg 4xl:max-w-5xl 4xl:text-xl">
          Polyglot Alpha v2 is an end-to-end pipeline of 10+1 verifiable components that turns
          foreign-language news into priced contracts on Polymarket V2 — with auctioned execution,
          adversarial translation debate, and an 11-judge consensus.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Button asChild>
            <Link href="/events">
              Explore live events
              <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
          </Button>
          <TriggerButton />
          <Button variant="ghost" asChild>
            <Link href="/leaderboard">
              Leaderboard
              <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
          </Button>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3 4xl:gap-6">
        {THESIS.map((t) => {
          const Icon = t.icon;
          return (
            <Card key={t.title} className="transition-colors hover:border-primary/40">
              <CardContent className="space-y-2 p-5">
                <Icon className="h-5 w-5 text-primary" aria-hidden />
                <h3 className="text-sm font-semibold">{t.title}</h3>
                <p className="text-xs leading-relaxed text-muted-foreground">{t.body}</p>
              </CardContent>
            </Card>
          );
        })}
      </section>

      <section className="space-y-3">
        <div className="flex items-end justify-between">
          <div>
            <h2 className="text-lg font-semibold">Pipeline architecture · 11 graph nodes across 7 lifecycle phases</h2>
            <p className="text-xs text-muted-foreground">
              Drag to pan, scroll to zoom. Nodes glow when their phase is running on the featured event.
            </p>
          </div>
        </div>
        <WorkflowOverview phases={getFeaturedPhases(featured)} />
      </section>

      <section className="space-y-3">
        <div className="flex items-end justify-between">
          <div>
            <h2 className="text-lg font-semibold">Featured events</h2>
            <p className="text-xs text-muted-foreground">
              Most recent runs — tap to drill into the 7-phase lifecycle, 11-node workflow.
            </p>
          </div>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/events">
              All events ({events?.length ?? 0}) →
            </Link>
          </Button>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {events?.slice(0, 3).map((e) => <EventCard key={e.id} event={e} />)}
        </div>
      </section>
    </div>
  );
}
