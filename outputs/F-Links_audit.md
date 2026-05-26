# F-Links — Link + Tooltip Audit (2026-05-26)

Working dir: `/Users/messili/codebase/polyglot-alpha`. Dev UI: `http://localhost:3001`.

## Phase 1 — Static enumeration

Total `<a>` / `<Link>` / `router.push` / `window.open` declarations: **28** across the codebase (excluding `node_modules`).

| File:line | Link text | href | Type |
|---|---|---|---|
| ui/app/layout.tsx:19 | Skip to main content | `#main-content` | Anchor — target verified (`<main id="main-content">`) |
| ui/components/shared/SiteHeader.tsx:14-19 | Overview / Events / History / Leaderboard / Operators / About | `/`, `/events`, `/history`, `/leaderboard`, `/operators`, `/about` | Internal — all routes exist (`ui/app/*/page.tsx`) |
| ui/components/shared/SiteHeader.tsx:31 | logo | `/` | Internal |
| ui/app/page.tsx:75, 82, 126 | CTA buttons | `/events`, `/leaderboard` | Internal |
| ui/app/operators/page.tsx:179 | View full leaderboard | `/leaderboard` | Internal |
| ui/app/agents/[address]/page.tsx:86 | back link | `/events` | Internal |
| ui/app/agents/[address]/page.tsx:103 | event card | `/events/${e.id}` | Dynamic — verified live |
| ui/app/events/[id]/page.tsx:105 | back | `/events` | Internal |
| ui/components/operators/RegisterOperatorCta.tsx:90 | Register your agent | `mailto:licaomeng@gmail.com?subject=…` | mailto |
| ui/app/about/page.tsx:112 | Operators | `/operators` | Internal |
| ui/app/about/page.tsx:191 | email | `mailto:licaomeng@gmail.com` | mailto |
| ui/app/about/page.tsx:198 | GitHub | `https://github.com/licaomeng/polyglot-alpha` | External — well-formed; **user action: confirm repo is public** |
| ui/app/history/page.tsx:128 | row link | `/events/${r.id}` | Dynamic |
| ui/components/reputation/LeaderboardTable.tsx:130 | agent profile | `/agents/${row.address}` | Dynamic — verified live for 3 seeders |
| ui/components/onchain/ArcExplorerEmbed.tsx:11 / TxLink.tsx:25 | arc tx | `https://testnet.arcscan.app/tx/${hash}` | External — well-formed |
| ui/components/polymarket/PolymarketDetail.tsx:89 | polymarket URL | `polymarket.com/dryrun/dryrun-…` | External — well-formed (mock URL in dryrun mode) |
| ui/components/event/TrustIndicators.tsx:63 | anchor tx | `arcTxUrl(anchorTx)` | External — well-formed |
| ui/components/event/TrustIndicators.tsx:100 | ipfs cid | `https://ipfs.io/ipfs/${ipfsCid}` | External — **WAS malformed**, fixed |
| ui/components/event/EventTimeline.tsx:292, 360 | pipeline trace / reasoning ipfs | `https://ipfs.io/ipfs/${ipfsCid}` | External — **STILL malformed**, deferred (F-Phase scope, cannot modify) |
| ui/components/TriggerButton.tsx:58, 90, 94 | router.push | `/events/${id}`, `/events` | Programmatic — verified |

Routes available: `/`, `/about`, `/agents/[address]`, `/events`, `/events/[id]`, `/history`, `/leaderboard`, `/operators`. All link destinations resolve.

## Phase 2 — Live verification

Probed via curl + Playwright:

| URL | HTTP | Render check | Result |
|---|---|---|---|
| `/` | 200 | Hero + TriggerButton present | OK |
| `/events` | 200 | Cards list (10 events) | OK |
| `/events/44` | 200 | Detail page renders Timeline+JudgePanel | OK |
| `/events/70` | 200 | Same | OK |
| `/history` | 200 | Filters + table render | OK |
| `/leaderboard` | 200 | Empty state when no data, table when data | OK |
| `/operators` | 200 | OperatorCards + RegisterCta | OK |
| `/about` | 200 | Static page renders | OK |
| `/agents/0x144d…Eb4A` | 200 | Seeder profile (`alias=Aurora-α-3`, reputation 0.85) | OK |
| `/agents/0x396B…51f4` | 200 | OK | OK |
| `/agents/0x5554…E7F6` | 200 | OK | OK |
| `#main-content` | n/a | `<main id="main-content">` exists | OK |
| `https://testnet.arcscan.app/tx/...` (multiple) | external | well-formed URL pattern | OK |
| `https://ipfs.io/ipfs/mock/91bbfd85a4ac` | external | well-formed after TrustIndicators fix | OK |
| `https://ipfs.io/ipfs/ipfs://mock/91bbfd85a4ac` | external | **MALFORMED** — double scheme prefix | Deferred (EventTimeline.tsx, F-Phase scope) |
| `polymarket.com/dryrun/dryrun-…` | external | well-formed | OK |
| `mailto:licaomeng@gmail.com` | mailto | matches surrounding "Commercial license + operator registration" copy | OK |
| `https://github.com/licaomeng/polyglot-alpha` | external | well-formed | OK; user action: confirm pushed/public |

### Broken links found (before this pass)

| File:line | Issue |
|---|---|
| `ui/components/event/TrustIndicators.tsx:100` | `https://ipfs.io/ipfs/${ipfsCid}` produced malformed URL when CID already begins with `ipfs://` scheme (e.g. `ipfs://mock/91bbfd85a4ac` → `https://ipfs.io/ipfs/ipfs://mock/91bbfd85a4ac`). **Fixed** — strip `^ipfs://` prefix before concatenation. |
| `ui/components/event/EventTimeline.tsx:292` | Same double-scheme bug on `translationDetails.pipeline_trace_ipfs`. **Deferred** — file owned by F-Phase. |
| `ui/components/event/EventTimeline.tsx:360` | Same double-scheme bug on `onchain.reasoning_ipfs`. **Deferred** — file owned by F-Phase. |

## Phase 3 — Tooltip audit

Tooltip components in use: `Tooltip` (`ui/components/ui/tooltip.tsx`), `MetricInfo` and `PhaseInfo` (`ui/components/event/MetricExplainer.tsx`).

Audited 19 explain-icon tooltips on `/events/70`:

| Tooltip | DOM wired | Render on hover |
|---|---|---|
| Explain phase Event Ingestion / USDC Auction / Translation Pipeline / 11-Judge Panel / On-chain Anchor / Polymarket V2 Submission / Streaming Revenue (7 phase explainers) | OK | OK |
| Explain auction formula | OK | OK |
| Explain BLEU, COMET, MQM | OK | **Was broken — fixed** |
| Explain D1–D8 (8 style judges) | OK | **Was broken — fixed** |

### Tooltip render failure (root cause)

`Tooltip` wrapper is `<span class="relative inline-flex group">` containing a 16px button + an absolutely-positioned `<span role="tooltip">`. The tooltip child had `max-w-xs` (max-width: 320px) but no explicit width. With `position: absolute`, browsers fall back to shrink-to-fit; because the parent inline-flex `span` is only ~16px wide, the tooltip's content width collapses to ~90px. At narrow viewports the tooltip became a 90px column ~324px tall — visually broken / unreadable. **Fixed** by adding `w-max` to the tooltip child class list (lets it take natural content width, capped by `max-w-xs`). After fix: BLEU tooltip on `/events/70` measures 320×100px — correct.

## Phase 4 — Fixes applied this pass

1. `ui/components/event/TrustIndicators.tsx` — strip `^ipfs://` prefix from `event.anchor.ipfsCid` before building the gateway URL. (~3 lines)
2. `ui/components/ui/tooltip.tsx` — add `w-max` to the tooltip child so it claims natural content width instead of collapsing into the wrapper span's column. (1 class added + 1 comment)

TypeScript: `npx tsc --noEmit` → exit 0.

## Action items for user

- **Confirm `https://github.com/licaomeng/polyglot-alpha` is pushed + public** — the `/about` page links to it (the link is well-formed but the target repo state is out of UI scope).
- The 2 malformed `ipfs.io/ipfs/ipfs://...` URLs in `EventTimeline.tsx` (lines 292, 360) need the same `replace(/^ipfs:\/\//, "")` fix; file is owned by F-Phase agent this pass — flag for next batch.
- `OperatorCard.tsx` has no link to `/agents/{address}` even though those pages exist and the seeder cards display the address — consider adding a link wrapper (UX miss, not a broken link).
