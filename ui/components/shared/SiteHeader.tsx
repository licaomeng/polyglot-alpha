"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Zap } from "lucide-react";
import { API_BASE } from "@/lib/api";

const IS_MOCK_MODE =
  typeof API_BASE === "string" &&
  (API_BASE.includes("localhost") || API_BASE.includes("127.0.0.1"));

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/events", label: "Events" },
  { href: "/history", label: "History" },
  { href: "/leaderboard", label: "Leaderboard" },
  { href: "/operators", label: "Operators" },
  { href: "/about", label: "About" },
];

export function SiteHeader() {
  const pathname = usePathname();
  return (
    <header
      className="sticky top-0 z-30 border-b border-border/60 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60"
      style={{ paddingTop: "env(safe-area-inset-top)" }}
    >
      <div className="container flex h-14 items-center gap-3 sm:gap-6">
        <Link
          href="/"
          className="flex min-h-[44px] items-center gap-2 font-mono text-sm font-semibold tracking-wide sm:min-h-0"
          aria-label="Polyglot Alpha home"
        >
          <span className="grid h-7 w-7 place-items-center rounded-md bg-primary/15 text-primary">
            <Zap className="h-4 w-4" aria-hidden />
          </span>
          <span>
            POLYGLOT<span className="text-primary">·α</span>
          </span>
          <span className="hidden font-mono text-[10px] uppercase tracking-wider text-muted-foreground sm:inline">
            v2
          </span>
        </Link>
        <nav className="flex flex-1 items-center gap-1 overflow-x-auto" aria-label="Primary">
          {NAV.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname?.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex items-center rounded-md px-3 py-1.5 text-sm transition-colors min-h-[44px] sm:min-h-[32px]",
                  active
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:bg-accent/10 hover:text-foreground",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="hidden items-center gap-2 text-xs text-muted-foreground lg:flex">
          <span className="relative flex h-2 w-2">
            <span
              className={cn(
                "absolute inline-flex h-full w-full rounded-full opacity-75",
                IS_MOCK_MODE
                  ? "animate-pulse bg-amber-400"
                  : "animate-ping bg-emerald-400",
              )}
            />
            <span
              className={cn(
                "relative inline-flex h-2 w-2 rounded-full",
                IS_MOCK_MODE ? "bg-amber-500" : "bg-emerald-500",
              )}
            />
          </span>
          <span className="font-mono">{IS_MOCK_MODE ? "local-mock" : "live"}</span>
        </div>
      </div>
    </header>
  );
}
