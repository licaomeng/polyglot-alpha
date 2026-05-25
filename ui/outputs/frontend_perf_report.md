# Frontend Performance + Bundle Audit

> Static-only audit. No dev server restart, no browser, no rebuilds. All file paths absolute relative to `/Users/messili/codebase/polyglot-alpha/ui`.

## Summary
- **Total `.next` size: 250 MB** — but this is a **dev build** (eval-source-map, unminified). A real `next build` was NOT in place at audit time.
- **Largest dev chunk: `.next/static/chunks/main-app.js` = 5.8 MB unminified** (with eval source maps; banner literally says "neither made for production nor for readable output").
- **`node_modules` size: 505 MB** (typical for a Next.js project; ~80% is `next`+`@next` toolchain + `viem` + `lucide-react` install footprint).
- **Production deps: 16** (lean).
- **TypeScript strict mode: YES** (`strict: true`), but no `noUnusedLocals` / `noUnusedParameters`.
- **Unused prod deps detected: 1 hard (`zustand`), 0 soft (`tailwindcss-animate` is referenced in `tailwind.config.ts`, `react-dom` is peer-used by Next).**
- **`'use client'` files: 22 / 45 tsx** (49% — reasonable for an interactive dApp UI).

## Findings

### Large chunks (DEV build; production numbers will be much smaller)
| Chunk | Size | What's in it (best guess) |
|---|---|---|
| `.next/static/chunks/main-app.js` | 5.8 MB | All client code + react + lucide + framer + xyflow + tanstack-query, glued together with eval source maps |
| `.next/static/chunks/app/page.js` | 2.7 MB | Home page (`app/page.tsx`) including landing-page motion components |
| `.next/server/vendor-chunks/next.js` | 1.8 MB | Next.js runtime (server bundle) |
| `.next/server/vendor-chunks/@xyflow.js` | 1.2 MB | React Flow server-side rendered |
| `.next/static/chunks/app/layout.js` | 812 KB | Layout + global providers (theme, query-client) |
| `.next/server/vendor-chunks/@tanstack.js` | 288 KB | React Query |
| `.next/server/vendor-chunks/tailwind-merge.js` | 196 KB | tailwind-merge (oversized — see below) |
| `.next/server/vendor-chunks/d3-*` (6 files) | ~432 KB total | d3 ecosystem pulled in transitively by xyflow + recharts |
| `.next/server/vendor-chunks/lucide-react.js` | 48 KB | Already tree-shaken — only 22 unique icons in source |

Note: the eval-source-map dev artifacts roughly inflate code 5–10× vs. minified prod output. Expect real client gzip totals in the 250–400 KB range, not multi-MB.

### Heavy deps
| Dep | `node_modules` install size | Where used | Tree-shake-friendly? |
|---|---|---|---|
| `viem` | 50 MB | `hooks/useArcExplorer.ts` (single function: `createPublicClient`/`http`) | Yes, but only one tiny call site — **massive overkill**. Pulls in `ox`, `@noble`, abis, etc. |
| `lucide-react` | 37 MB (install only) | 17 imports, 22 unique icons | Yes via ESM named imports (already correct). Final bundle ~48 KB. |
| `recharts` | 5.3 MB → pulls in d3-* (~432 KB) | 3 files: `leaderboard`, `BuilderFeeStream`, `ReputationHistory` | Partially — `ResponsiveContainer` drags d3 transitively |
| `@xyflow/react` | 4.6 MB → 1.2 MB chunk | `components/workflow/WorkflowOverview.tsx` + `PhaseNode` | Limited — heavy by design, loaded eagerly today |
| `@tanstack/react-query` | 4.7 MB → 288 KB chunk | 5 imports | Good ESM, fine |
| `framer-motion` | 3.9 MB | 7 components | Good with ESM, but consider `LazyMotion` + `domAnimation` to ship ~60% less |

### Unused prod deps (candidates to remove)
- **`zustand`** — 0 imports across `app/`, `components/`, `hooks/`, `lib/`. Remove.
- `tailwindcss-animate` — used in `tailwind.config.ts` plugins. **Keep**, but it should arguably be a `devDependency`.
- `react-dom` — implicit peer of Next, never imported directly in source. Keep (required at runtime).

### Image / asset issues
- `public/` directory **does not exist**. Zero static images, fonts, or icons. No optimization opportunity here, but: no favicon, no OG image — UX gap, not perf gap.

### TypeScript strictness gaps
- `strict: true` is on (so `noImplicitAny`, `strictNullChecks`, etc. are implied), good.
- **Missing**: `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch`, `noImplicitReturns`. Add for dead-code hygiene.
- No `incremental` config tuning; `tsconfig.tsbuildinfo` is 234 KB — fine.

### `next.config.mjs` gaps (significant)
The entire config is:
```js
{ reactStrictMode: true, env: { NEXT_PUBLIC_API_BASE: ... } }
```
Missing:
- `images: { ... }` — not blocking yet (no `public/` images), but required if/when added.
- `experimental.optimizePackageImports: ['lucide-react', 'recharts', 'framer-motion', '@xyflow/react']` — Next 14 supports this and would shrink the initial JS noticeably.
- `compiler: { removeConsole: { exclude: ['error', 'warn'] } }` for prod.
- `productionBrowserSourceMaps: false` (default, but worth being explicit).
- No `@next/bundle-analyzer` wiring — no visibility into chunk composition.

### Component / render heuristics
- 22/45 components are client (49%). Reasonable. Server-component-eligible candidates exist (e.g. layout, static cards) but most pages need interactivity.
- `useEffect` count across `app/components/hooks`: **2** total — very low, no render-loop risk.
- `useState` count: **7** total.
- `useArcExplorer.ts` calls `createPublicClient` inside a `useEffect`; benign but `viem` is pulled into the client bundle just for `getBlockNumber()` — a `fetch` against the RPC endpoint would shave megabytes.

### CSS / Tailwind audit
- 260 `class(Name)?=` occurrences across source — modest.
- Color palette unified around 4 neon accents (cyan/magenta/lime/amber) + semantic emerald/amber/fuchsia/sky. Defined in `tailwind.config.ts`, no hardcoded RGB.
- No custom `@layer` rules audited; CSS output: `layout.css` 36 KB + `page.css` 19 KB (pre-purge sizes; final prod CSS will be <10 KB gzipped).

## Bundle size recommendations (ranked by impact)

1. **Replace `viem` with raw `fetch` in `hooks/useArcExplorer.ts`.** Single call site (`getBlockNumber`) does not justify a 50 MB install / multi-hundred-KB bundle hit. JSON-RPC `eth_blockNumber` is one POST request. **Estimated client JS savings: 150–300 KB gzip.**

2. **Run an actual production build.** Current `.next/` is a dev artifact with eval source maps. Cannot give true gzip numbers without `next build`. Add `@next/bundle-analyzer`:
   ```js
   // next.config.mjs
   import withBundleAnalyzer from '@next/bundle-analyzer'
   export default withBundleAnalyzer({ enabled: process.env.ANALYZE === 'true' })(nextConfig)
   ```

3. **Enable `experimental.optimizePackageImports`** for `lucide-react`, `recharts`, `framer-motion`, `@xyflow/react` in `next.config.mjs`. Next 14.2 native, zero-effort, typically 10–25% off initial JS.

4. **Lazy-load `@xyflow/react` and `recharts` via `next/dynamic`** on the routes that use them (`/workflow`, `/leaderboard`, `/`). Both are render-blocking today and only needed on specific pages. Expected savings: 200–400 KB off the shared chunk.

5. **Switch `framer-motion` to `LazyMotion` + `m` + `domAnimation`** in components that only use `motion.div` (7 files). Cuts framer-motion bundle ~60%.

6. **Remove `zustand` from `package.json`** (unused). Move `tailwindcss-animate` to `devDependencies`.

7. **Tighten `tsconfig.json`**: add `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns` to catch dead code before it ships.

8. **Add a `public/` favicon + OG image** when adding marketing polish — currently absent.

## Caveats
- `.next/` was a dev build, so all "chunk size" numbers above are dev-mode upper bounds, not what users download. Real prod bundle is likely 5–10× smaller after minification + DCE + scope hoisting.
- No bundle-analyzer report was available, so per-chunk composition is inferred from import sites and `vendor-chunks/` names.
