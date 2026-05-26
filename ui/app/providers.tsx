"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Suspense, useState } from "react";
import { PhaseStateProvider } from "@/hooks/usePhaseState";
import { ModeProvider } from "@/contexts/ModeContext";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 1000 * 30,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
      <QueryClientProvider client={queryClient}>
        {/*
          `ModeProvider` reads `useSearchParams()` to honor `?mode=` deep
          links — Next 15 requires this to be wrapped in a `Suspense`
          boundary so server-rendered shells don't bail out the entire
          subtree during static generation.
        */}
        <Suspense fallback={null}>
          <ModeProvider>
            <PhaseStateProvider>{children}</PhaseStateProvider>
          </ModeProvider>
        </Suspense>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
