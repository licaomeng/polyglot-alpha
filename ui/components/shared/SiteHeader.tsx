"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Zap, FlaskConical } from "lucide-react";
import { useDemoMode } from "@/contexts/ModeContext";
import { DemoModeToggle } from "@/components/DemoModeToggle";

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
  const { mode } = useDemoMode();
  const isMock = mode === "mock";
  return (
    <header
      className={cn(
        "sticky top-0 z-30 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60 transition-colors",
        isMock
          ? "border-b border-amber-500/50 bg-amber-500/[0.04]"
          : "border-b border-primary/30",
      )}
      style={{ paddingTop: "env(safe-area-inset-top)" }}
      data-mode={mode}
    >
      <div className="container flex h-14 items-center gap-3 sm:gap-6">
        <Link
          href="/"
          className="flex min-h-[44px] items-center gap-2 font-mono text-sm font-semibold tracking-wide sm:min-h-0"
          aria-label="Polyglot Alpha home"
        >
          <span
            className={cn(
              "grid h-7 w-7 place-items-center rounded-md transition-colors",
              isMock
                ? "bg-amber-500/20 text-amber-400"
                : "bg-primary/15 text-primary",
            )}
          >
            {isMock ? (
              <FlaskConical className="h-4 w-4" aria-hidden />
            ) : (
              <Zap className="h-4 w-4" aria-hidden />
            )}
          </span>
          <span>
            POLYGLOT
            <span
              className={cn(
                "transition-colors",
                isMock ? "text-amber-400" : "text-primary",
              )}
            >
              ·α
            </span>
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
                    ? isMock
                      ? "bg-amber-500/15 text-amber-400"
                      : "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:bg-accent/10 hover:text-foreground",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <div className="hidden items-center gap-2 text-xs lg:flex" aria-live="polite">
            <span className="relative flex h-2 w-2">
              <span
                className={cn(
                  "absolute inline-flex h-full w-full rounded-full opacity-75",
                  isMock ? "animate-pulse bg-amber-400" : "animate-ping bg-emerald-400",
                )}
              />
              <span
                className={cn(
                  "relative inline-flex h-2 w-2 rounded-full",
                  isMock ? "bg-amber-500" : "bg-emerald-500",
                )}
              />
            </span>
            <span
              className={cn(
                "font-mono uppercase tracking-wider transition-colors",
                isMock ? "text-amber-400" : "text-emerald-400",
              )}
            >
              {isMock ? "mock" : "live"}
            </span>
          </div>
          <DemoModeToggle />
        </div>
      </div>
    </header>
  );
}
