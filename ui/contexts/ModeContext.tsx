"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { usePathname, useSearchParams } from "next/navigation";

export type DemoMode = "live" | "mock";

/**
 * localStorage key used to persist the user's preferred demo mode across page
 * reloads. Scoped under the `polyglot:` prefix so it cannot collide with any
 * other application that might be served from the same origin during dev.
 */
const STORAGE_KEY = "polyglot:mode";

const VALID_MODES: readonly DemoMode[] = ["live", "mock"] as const;

function isDemoMode(value: unknown): value is DemoMode {
  return typeof value === "string" && (VALID_MODES as readonly string[]).includes(value);
}

interface ModeContextValue {
  mode: DemoMode;
  setMode: (next: DemoMode) => void;
}

const ModeContext = createContext<ModeContextValue>({
  mode: "live",
  setMode: () => {},
});

/**
 * Read the initial mode synchronously during the first client render so the
 * very first paint already reflects either the URL `?mode=` query parameter
 * or the previously-persisted localStorage value. Falls back to `"live"`.
 *
 * Order of precedence:
 *   1. URL `?mode=` query parameter (deep-linkable demo).
 *   2. `localStorage["polyglot:mode"]` (sticky user preference).
 *   3. `"live"` (safe default — never silently demo with synthetic data).
 */
function readInitialMode(): DemoMode {
  if (typeof window === "undefined") return "live";
  try {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("mode");
    if (isDemoMode(fromUrl)) return fromUrl;
    const fromStorage = window.localStorage.getItem(STORAGE_KEY);
    if (isDemoMode(fromStorage)) return fromStorage;
  } catch {
    // localStorage / URL access may throw in restricted sandboxes — silently
    // fall through to the safe default.
  }
  return "live";
}

export function ModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<DemoMode>(() => readInitialMode());
  const [announcement, setAnnouncement] = useState<string>("");
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const setMode = useCallback((next: DemoMode) => {
    // Always persist to localStorage, even when the in-memory mode is
    // unchanged. This makes URL-driven mode switches (?mode=mock) reliably
    // sticky on refresh, including the first time the user lands on a deep
    // link before localStorage has ever been seeded.
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore storage failures — context still updates in memory
    }
    setModeState((prev) => {
      if (prev === next) return prev;
      setAnnouncement(`Switched to ${next} mode`);
      return next;
    });
  }, []);

  // Re-read the URL `?mode=` parameter on every navigation change. When the
  // user edits the URL directly (or follows a deep link), surface that as the
  // new mode and persist it to localStorage so it sticks after the param is
  // removed. We deliberately do NOT push URL changes when the toggle is
  // clicked — the toggle owns localStorage only, the URL stays clean.
  useEffect(() => {
    const fromUrl = searchParams?.get("mode");
    if (!fromUrl) return;
    if (!isDemoMode(fromUrl)) return;
    setMode(fromUrl);
    // Intentionally exhaustive deps: rerun whenever the route or query changes.
  }, [pathname, searchParams, setMode]);

  const value = useMemo<ModeContextValue>(() => ({ mode, setMode }), [mode, setMode]);

  return (
    <ModeContext.Provider value={value}>
      {children}
      {/*
        Live region for assistive tech — announces mode changes politely so
        screen reader users get the same feedback sighted users do from the
        segmented control color swap.
      */}
      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>
    </ModeContext.Provider>
  );
}

/**
 * Read the current demo mode + a setter from the surrounding `ModeProvider`.
 * Callers in client components can drive UI off `mode` (e.g. tint headers,
 * conditionally render badges) and dispatch `setMode` from controls.
 */
export function useDemoMode(): ModeContextValue {
  return useContext(ModeContext);
}
