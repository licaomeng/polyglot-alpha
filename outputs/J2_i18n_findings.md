# J2 — i18n Picky Tester Findings

Agent: J2 (read-only, headless Playwright)
Date: 2026-05-26
Viewport: 1280x900 + mobile 375x812 sweep
Backend: http://localhost:8000 | UI: http://localhost:3001
Events triggered: 5 (IDs 74–78); 1 pre-existing combo event reused (ID 73)

## Pass / Fail summary

| # | Test | Result | Severity |
|---|------|--------|----------|
| 1 | Long Chinese title (~45 CJK chars) | PARTIAL | LOW |
| 2 | Arabic RTL title | FAIL | HIGH |
| 3 | Emoji + XSS payload | PASS (XSS) / PASS (emoji) | — |
| 4 | 500-char URL | PASS (not rendered anywhere) | LOW |
| 5 | Empty sources / null language | PASS | — |
| 6 | Number formatting consistency | PARTIAL | MED |
| 7 | Locale-aware dates / timezone | FAIL | HIGH |
| 8 | Address truncation + tooltip + copy | PARTIAL | MED |

Pass rate: 3 PASS, 4 PARTIAL, 1 FAIL out of 8.

---

## HIGH severity findings

### H1. Timezone bug — every relative timestamp is wrong outside UTC
**Test 7. Confirmed reproducer.**
- Backend `/events` returns `triggered_at: "2026-05-26T07:23:58.980735"` — naive ISO, **no `Z` and no offset**.
- Browser `new Date("2026-05-26T07:23:58.980735")` per ECMA-262 §21.4.3.2 treats the string as **local time** when the offset is absent.
- In Singapore (UTC+8) this puts every just-triggered event 8 hours in the past, so the UI labels events created 30 seconds ago as `8h ago`.
- Reproducer (Playwright `evaluate`):
  ```
  new Date('2026-05-26T07:23:58.980735').toISOString()
  → 2026-05-25T23:23:58.980Z  (8h shift)
  ```
- Impact: every non-UTC viewer sees wrong "x ago" labels on the Events list (also "ingested 8h ago" on detail header). Cross-region demo (recruiter outside UTC) will see "8h ago" on the event you just triggered live.
- Fix: backend must emit `...Z` (UTC) or `+00:00`; alternatively UI must append `Z` before parsing.
- Screenshot: `outputs/J2_screenshots/01_events_list.png` (all 5 fresh events labeled `8h ago`).

### H2. RTL not detected — Arabic title renders LTR, left-aligned
**Test 2.**
- Title H3 / H1 always have `dir="ltr"` and `text-align: start` (which is LEFT in LTR context). Arabic / Hebrew titles inherit LTR layout.
- DOM verified: `dir: "ltr"`, `unicode-bidi: isolate`, no `lang` attribute set anywhere on the title element. No per-character bidi reordering happens beyond Unicode's own RTL run handling, but the **paragraph base direction is wrong**, which is what RTL users notice (question marks/punctuation land on the LTR side, alignment off).
- Arabic event title `"هل ستتخذ الحكومة قراراً بشأن السياسة النقدية قبل ٣١ ديسمبر ٢٠٢٦؟"` (event 75) is left-aligned with the trailing `؟` floating to the right of the wrapped line.
- Same combo on event 73 (`"<script>alert(1)</script> 🎉 مرحبا עברית"`) — h1 dir=ltr, mixed Arabic + Hebrew + Latin all flowed LTR.
- Fix: set `dir="auto"` on any user-supplied text container (title H3 / H1, source name, source URL, content_hash). Optional: detect with `@formatjs/intl-localematcher` or first-strong-char heuristic and set `lang`.
- Screenshots:
  - `outputs/J2_screenshots/01_events_list.png` — list view, Arabic card left-aligned.
  - `outputs/J2_screenshots/02_event73_xss_rtl_emoji.png` — detail page, Arabic+Hebrew h1 LTR.

---

## MED severity findings

### M1. Address truncation: no tooltip, no copy, no consistency with tx-hash
**Test 8.**
- Truncation pattern for agent address: `0x144d…Eb4A` (4 hex + ellipsis + 4 hex).
- Truncation pattern for tx hash on same page: `0x46308580…3b8704` (8 hex + ellipsis + 6 hex). **Two different policies on one page.**
- No `title=` or `aria-label=` containing the full address on any inspected `<span>`/`<code>`/`<td>` — hover reveals nothing, screen reader announces the truncated form only.
- On event detail (e.g. `/events/73`) auction table addresses are plain text — **not clickable**, no `<a>` wrapping them, no copy-to-clipboard control. Users can't drill from an event's auction row into the agent.
- On `/leaderboard` the SAME address IS wrapped in `<a href="/agents/0x928a...9390">` — so address linking exists but is inconsistent across surfaces.
- Case is preserved as-is from the backend (`0x396B…51f4` mixed, `0xa934…beb1` lower, `0x144d…Eb4A` mixed). EIP-55 checksum casing is OK to keep verbatim, but it makes visual "same agent" matching harder.
- `/agents/{address}` does not render its own profile page — it redirects to `/events/73` (most recent event for that agent?). For an unknown agent it bounces to `/leaderboard`. Either is fine but undocumented.

### M2. Number / unit formatting is mixed
**Test 6.**
- Bid amounts: `$0.75`, `$0.65`, `$0.32`, `$5.00` — consistent 2-decimal USD-style across the auction table. **Good.**
- Reputation displayed as decimal `0.85` / `0.92` / `0.00` in detail page auction table AND in leaderboard "Rep." column.
- Win rate displayed as `%`: `69%`, `52%`, `16%`, `100%` on leaderboard.
- So a probability-style quantity (rep) and another probability-style quantity (win rate) appear in the **same table row** with different conventions (decimal vs percent). Pick one.
- USDC values shown to 2 decimals (`$0.50`), not 6 — fine for display but worth noting because USDC supports 6dp on-chain. No `0.4999999…` floats spotted in any view.
- No long decimals (`> 5dp`) leaked into the DOM anywhere.
- Currency symbol is always bare `$` — no locale prefix, no `USDC` suffix on the auction table column header (header says `BID (USDC)` though, which clarifies it).

### M3. Cards lack `lang`/`dir=auto` — CJK relies on default wrap behavior
**Test 1.**
- The 45-CJK-char Chinese title (`中国人民银行宣布2026年7月31日前下调存款准备金率50个基点以支持房地产市场和稳定金融秩序`) wraps cleanly at 1280px (1 line, h1 width 1248px) and at 375px mobile (3 lines, height 66px in card; no horizontal overflow at body level).
- `word-break: normal` + `overflow-wrap: normal` is good enough for CJK because the browser breaks between ideographs. But there is **no `word-break: break-word` fallback for languages where the browser cannot break** (long Thai run, long German compound, or a 60-char unbroken Latin token in a title) — these would overflow horizontally. Not directly hit in these tests, but a latent risk.
- Cards also have `text-overflow: clip` and no `line-clamp` — so any user-supplied title of arbitrary length will keep growing the card vertically (which is the case the team has — heights ranged 22→44→66px across the test set). This is graceful in practice but means card grid heights are jagged.

---

## LOW / non-issues

### L1. XSS — properly escaped (PASS)
- Title `"🚀 ... <script>alert(\"xss\")</script>"` and the legacy event 73 `"<script>alert(1)</script>"` are rendered as **text** in DOM (`&lt;script&gt;alert(1)&lt;/script&gt;`). No raw `<script>` in body HTML. React's default escaping is doing its job.
- Verified: `body.innerHTML.includes('<script>alert')` → `false`; `body.innerHTML.includes('&lt;script&gt;')` → `true`.

### L2. Emoji renders (PASS)
- 🚀 and 🎉 render correctly in card titles and h1; no tofu boxes.

### L3. Empty / null fields handled (PASS)
- `sources: []` → card shows the placeholder string `"unknown source"` (not `"undefined"` / blank). Detail page shows `Source:` followed by nothing — slightly bare but no leaked `null`.
- `language: null` is **rejected by FastAPI Pydantic validator** with `string_type` error (HTTP 422). So the UI never receives `null` language. Tested with `language` omitted instead — pipeline accepts and event 78 was created without UI artifacts.
- No `undefined` / `null` / `NaN` strings found in body innerText on homepage, events list, or any reached detail page.

### L4. Long URL doesn't reach the UI (PASS)
- The 500-char `https://example.com/aaaa...` URL is sent in the trigger payload and persisted by the backend, but the events list and detail page only render the source **name** (`longsrc`). I could not find a single rendered element containing the long URL. So no layout risk in the current UI; if a future change exposes `source.url`, M3-style `word-break: break-all` will be needed on the anchor.

---

## Locale / i18n readiness verdict

US-only (English). Concrete gaps in priority order:

1. **No locale-aware time formatting** — only English relative-time strings (`8h ago`, `30s elapsed`, `~5s remaining`, `~1m`), and even those are wrong outside UTC (H1).
2. **No `dir=auto` / `lang` on any user-supplied text** — RTL content is left-aligned and bidi-broken (H2).
3. **No `Intl.NumberFormat`** — all `$x.xx` strings are hand-formatted, no locale grouping separators, no localizable currency.
4. **No translation layer** — all UI labels (`Mock`, `Queued`, `Settled`, `Phase timeline`, `BID (USDC)`, `WINNER`, `awaiting verdict`) are hardcoded English. No `next-intl` / `react-intl` / `next-i18next` in `package.json` based on file tree.
5. Strengths: XSS-safe, emoji-safe, CJK wraps OK, empty-state strings ("unknown source") avoid `undefined` leaks.

One sentence: **US-only — the app is XSS- and emoji-clean and survives CJK input visually, but it does not actually localize times (timezone bug), text direction (Arabic/Hebrew flow LTR), or any string, and address handling is inconsistent across surfaces.**

---

## Files

- Findings (this file): `/Users/messili/codebase/polyglot-alpha/outputs/J2_i18n_findings.md`
- Screenshots: `/Users/messili/codebase/polyglot-alpha/outputs/J2_screenshots/`
  - `01_events_list.png` — events list, all 5 fresh events labeled `8h ago` (H1), Arabic card LTR (H2)
  - `02_event73_xss_rtl_emoji.png` — event detail with XSS escaped, mixed RTL h1 LTR (H2)
  - `03_event74_chinese.png` — Chinese title detail, no overflow at 1280px
  - `04_events_mobile_375.png` — mobile 375px viewport, CJK wraps to 3 lines OK
  - `05_leaderboard_addresses.png` — leaderboard with 4+4 truncation + agent links
  - `06_event73_auction_addresses.png` — auction table with non-clickable addresses + 8+6 tx truncation (M1)
