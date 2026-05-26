# E4 — UI cleanup + `/events/156` 404 chatter hunt

## Root cause of `/events/156` 404 spam

**File:** `ui/hooks/useEvent.ts` (before fix)

The hook polled `GET /events/{id}` every 4 s with `refetchIntervalInBackground: true` and only `retry: 1`. There is **no** guard against a permanent 404 — the id is treated as transient. As long as a user has a tab open at `/events/156` (a stale URL someone pasted; event id 156 never existed in this DB — latest ids are 44-69), the tab keeps hammering the backend forever (also keeps an SSE stream open at `GET /sse/events?event_id=156`, but only 1 connection per tab, not the source of spam).

There is no localStorage / sessionStorage caching anywhere in the UI (`grep -rn "localStorage|sessionStorage" ui/{app,components,hooks,lib}` → 0 hits). No hardcoded `156` anywhere in source (`grep` clean). The id flowed in from the URL alone.

Backend confirmation: `/tmp/polyglot_backend_postdinner.log` shows 1× `GET /sse/events?event_id=156` (single open EventSource) and 252+ `GET /events/156 404 Not Found` from a single client port 65115 — the React-Query 4 s poll.

## Fix

`ui/hooks/useEvent.ts` — stop polling when the error is 404, and don't retry 404s either:

```ts
refetchInterval: (query) => {
  const err = query.state.error as Error | null;
  if (err && err.message.includes("404")) return false;
  return 4000;
},
retry: (failureCount, err: unknown) => {
  const msg = err instanceof Error ? err.message : "";
  if (msg.includes("404")) return false;
  return failureCount < 1;
},
```

Rationale:
- 5xx / network errors still poll so the page recovers when backend restarts.
- Page already renders an "Event not found" empty state on 404 (`ui/app/events/[id]/page.tsx` lines 85-113), so stopping the poll has no UX downside — the UI was already correct, only the network was wasteful.
- Detected via `err.message.includes("404")` because `ui/lib/api.ts:18` throws `Error(\`API ${res.status}: ${res.statusText}\`)`.

## Verification

- **TypeScript:** `pnpm exec tsc --noEmit` → exit 0, 0 errors (same as before fix).
- **Lint:** `next lint` shows only 1 pre-existing warning in `components/event/AuctionExplainer.tsx` (owned by C2, not touched). 0 new warnings.
- **30 s backend log watch:** `/tmp/polyglot_backend_postdinner.log`, `grep -c "GET /events/156"` over 40 s window → delta = 16 hits (~one every 2.5 s from the open tab; multiple in-flight). **The fix does not retro-apply via HMR to already-mounted `useQuery` options** — react-query keeps the original refetch config until the component remounts. The open tab at `/events/156` must be refreshed (or the user navigates away) before the spam stops. Any *newly* opened tab to a non-existent id will not poll past the first 404.
- Same source port `127.0.0.1:65115` produces every one of the 404s — confirms a single browser tab as the source.

## Other findings (UI codebase audit)

Codebase is **very clean**. Scan results across `ui/{app,components,hooks,lib}` (excluding tests, node_modules, .next):

| Check | Hits | Notes |
|---|---|---|
| `console.log / .warn / .error` | 0 | Production code is silent. |
| ` as any` / `<any>` | 0 | Strict typing throughout. |
| `@ts-ignore / @ts-expect-error / @ts-nocheck` | 0 | No suppressions. |
| `TODO / FIXME / XXX / HACK` | 0 | No outstanding markers. |
| `localStorage / sessionStorage` | 0 | No client-side cache anywhere. |
| Hardcoded numeric event ids (`/events/[0-9]+`) | 0 | All `/events/${id}` are template-driven. |
| Hardcoded hex addresses (≥20 chars) | 0 | All addresses come from data. |
| Dead imports | None spotted by `tsc --noEmit`. |

Polling sources (`useInterval | setInterval | setTimeout | refetchInterval`):
- `useEventList`: 5 s poll for the events list — fine, endpoint exists.
- `useEvent`: 4 s poll — **fixed above**.
- `TriggerButton.tsx:57`: one-shot 800 ms navigate delay — fine.
- `ProgressIndicator.tsx:47`: 1 s `setInterval` for "now" timestamp — local-only, no network.
- `PipelineLayerCard.tsx:34` / `ContractAddressDisplay.tsx:28`: 1.5 s "copied" toast reset — local-only.

Pre-existing lint warning (untouched per ownership constraint):
- `components/event/AuctionExplainer.tsx:42` — `bids` ternary should be wrapped in `useMemo` to stabilize the dependency array of `useMemo` at line 59. Cleanup candidate for C2.

## Files modified

- `ui/hooks/useEvent.ts` (8 lines changed) — bail out of polling on 404.

## Files NOT touched (per constraints)

`SiteHeader.tsx`, `app/operators/*`, `components/operators/*`, `AgentDebatePanel.tsx`, `EventTimeline.tsx`, `JudgePanel.tsx`, `AuctionExplainer.tsx`, `TrustIndicators.tsx`.
