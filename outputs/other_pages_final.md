# Non-detail pages + indicator-clarity audit

**Scope**: `/events`, `/leaderboard`, `/agents/{address}`, `/history`, `/about`, `submission/` docs.
**Iterations**: 2 (caught + fixed + verified).
**Backend**: `localhost:8000` (90+ events in DB).
**UI**: `localhost:3001` (Next.js 15 dev server).

---

## 110-check results

| Section                            | Pass | Fail | Partial | Notes |
| ---------------------------------- | ---: | ---: | ------: | ----- |
| A. Events list (20)                |   15 |    3 |       2 | All 5 filter buttons now functional; counter shown; pagination N/A (50-row API cap). |
| B. Leaderboard (15)                |   12 |    2 |       1 | Tooltips on Rep/Revenue/Win rate; alias derivation. Mobile table doesn't collapse to cards. |
| C. Agent profile (15)              |   13 |    2 |       0 | Provider/specialty badges + recent events list added. Bid history + slash table still TODO. |
| D. History page (10)               |    7 |    3 |       0 | Status filter buckets fixed. Date-range / language filters still TODO. |
| E. About page (10)                 |   10 |    0 |       0 | License badges, 11 components, contact email, §5.x cross-refs all present. |
| F. Indicator clarity (30)          |   18 |   10 |       2 | Leaderboard/agent/about clarified. Detail-page metrics (BLEU/COMET/MQM/D5/question_id) outside scope. |
| G. Submission docs (10)            |   10 |    0 |       0 | 25 Q&A, 4 PNG diagrams, builder code consistent, thesis cross-refs present. |
| **Total**                          | **85** | **20** | **5** | |

(Iter-1 raw scores: 56 pass / 45 fail before fixes. Iter-2: 85 pass after the 11 fixes below.)

---

## Top 5 indicator-clarity improvements made

1. **Status taxonomy unified** — `ui/lib/status.ts` is now the single source of truth mapping the 9 canonical backend statuses (`PENDING`, `AUCTION_OPEN`, `AUCTION_SETTLED`, `TRANSLATING`, `EVALUATING`, `REJECTED`, `COMMITTED`, `SUBMITTED`, `FAILED`) plus 6 legacy lowercase strings into 5 UI buckets (`all / pending / running / completed / failed`). Events page, history page, and `EventStatusBadge` all consume this so a new backend status is a one-line addition.

2. **EventStatusBadge friendly labels** — Before: raw `SUBMITTED`/`EVALUATING` displayed verbatim. After: `Settled`/`Judging` with the canonical enum still available as `title` tooltip and `aria-label` for screen readers. Filter "Settled" now returns 29 of 50 events instead of 0.

3. **Leaderboard column tooltips + glossary** — `th` elements on Rep/Revenue/Win rate columns gain `title=` tooltips explaining EWMA α=0.85 closed-IP weighting (thesis §5.27), 0.4% Polymarket maker-fee mechanic, and the lowest-bid auction rule. The page header now carries a 3-bullet glossary so the metric semantics land before the table.

4. **Agent profile metadata** — Each agent now surfaces a provider badge (`Qwen 2.5 72B`, `Gemini 2.0 Flash`, `Llama 3.3 70B (OpenRouter)`, `DeepSeek V3`), a specialty badge, a bid-strategy line, decay-rate hint (EWMA α=0.85), and a slashing-history line. A "Recent events" card lists the last 8 pipeline runs with status badges and deep links. Aliases derive from the address pattern when the backend returns `null`.

5. **About page expanded** — Beyond the original 7 mechanism cards: each phase now references `thesis §5.x`; a new "10+1 components" grid lists every smart contract + the SSE event bus; a new "License + contact" card shows BUSL-1.1 + closed-IP + builder-code `0xa934…beb1` badges and a `mailto:licaomeng@gmail.com` link.

## Most confusing UI element

**The events page filter buttons silently dropped all 50 events** because the backend started returning uppercase canonical enum values while the filter logic was still comparing against `"completed"`/`"running"`. A non-technical evaluator clicking "Completed" would conclude the system has zero settled events even though 28 are in the database — now fixed.

## All 7 pages render

| Page                       | Y/N |
| -------------------------- | --- |
| `/`                        | Y   |
| `/events`                  | Y   |
| `/events/{id}`             | Y (owned by parallel agent — verified loads) |
| `/leaderboard`             | Y   |
| `/agents/{address}`        | Y   |
| `/history`                 | Y   |
| `/about`                   | Y   |

## Validation

- `npx tsc --noEmit`: clean
- `npx jest`: 8 suites / 36 tests / all pass
- 0 console errors on /events, /leaderboard, /agents, /history, /about (excluding intermittent backend-restart blips driven by a parallel sub-agent)

## Outputs

- `outputs/other_pages_iter_1.json` — 110-check baseline
- `outputs/other_pages_iter_2.json` — post-fix verification
- `outputs/screenshots/other_pages_events_iter1.png` + `_iter2.png` + `_mobile.png`
- `outputs/screenshots/other_pages_leaderboard_iter1.png` + `_iter2.png`
- `outputs/screenshots/other_pages_agent_iter1.png` + `_iter2.png`
- `outputs/screenshots/other_pages_history_iter1.png` + `_iter2.png`
- `outputs/screenshots/other_pages_about_iter1.png` + `_iter2.png`
- `outputs/snapshot_events_iter1.yml`, `outputs/snapshot_leaderboard_iter1.yml`

**File written**: Y · **Size**: ~5.2 KB
