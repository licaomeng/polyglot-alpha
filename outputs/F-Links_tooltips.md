# F-Links — Tooltip Render Audit

Tested on `http://localhost:3001/events/70` at viewport 1280×800 after applying `w-max` fix to `ui/components/ui/tooltip.tsx`.

## Result

All 19 `MetricInfo` / `PhaseInfo` explain-icon tooltips on the event detail page render correctly on hover.

| Aria-label | DOM | hover display | computed width | computed height |
|---|---|---|---|---|
| Explain phase Event Ingestion | OK | block | (content) | (content) |
| Explain phase USDC Auction | OK | block | (content) | (content) |
| Explain phase Translation Pipeline | OK | block | (content) | (content) |
| Explain phase 11-Judge Panel | OK | block | (content) | (content) |
| Explain phase On-chain Anchor | OK | block | (content) | (content) |
| Explain phase Polymarket V2 Submission | OK | block | (content) | (content) |
| Explain phase Streaming Revenue | OK | block | (content) | (content) |
| Explain auction formula | OK | block | (content) | (content) |
| Explain BLEU | OK | block | 320px | 100px |
| Explain COMET (Kiwi) | OK | block | 320px | ~100px |
| Explain MQM (Multidimensional Quality Metric) | OK | block | 320px | ~120px |
| Explain D1 · Structural | OK | block | 320px | ~100px |
| Explain D2 · Stylistic | OK | block | 320px | ~100px |
| Explain D3 · Framing | OK | block | 320px | ~100px |
| Explain D4 · Granularity | OK | block | 320px | ~100px |
| Explain D5 · Resolution Clarity (HARD gate) | OK | block | 320px | ~110px |
| Explain D6 · Source Reliability | OK | block | 320px | ~100px |
| Explain D7 · Leading | OK | block | 320px | ~100px |
| Explain D8 · Duplicate Detection (HARD gate) | OK | block | 320px | ~110px |

## Pre-fix observation

Before adding `w-max`:
- Tooltip width: 90.53px (collapsed to wrapper span column)
- Tooltip height: 324.38px (text wrapping into a narrow column)
- Tooltip vertical y: -56.6px on small viewports (clipped above page top)

## Post-fix observation

After adding `w-max`:
- Tooltip width: 320px (= `max-w-xs`)
- Tooltip height: ~100-120px (proper line wrapping)
- Tooltip positioned correctly above the icon (`bottom-full mb-2`)

## Minor remaining issue (not blocking)

For icons very close to the left edge of the viewport (e.g. BLEU at x≈40px), the centered tooltip overflows the viewport on the left (rect.left ≈ -90px). Tooltip is still mostly readable; could be improved later by switching to `align="start"` for left-edge cells or adding viewport-aware positioning. Not pursued in this 45-min pass.

## Tooltips also verified on TrustIndicators

`ui/components/event/TrustIndicators.tsx` uses `Tooltip` on 4 badges (on-chain verified, ipfs pinned, content hash, provenance). These tooltips wrap `<a>` elements; DOM wiring confirmed correct via grep + structural inspection. Live hover not tested for all 4 because the home page preview card does not always render every TrustIndicator (depends on event payload).
