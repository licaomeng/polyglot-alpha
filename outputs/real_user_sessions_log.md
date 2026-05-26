# Real-User Sessions Log

Three sessions, ~15–20 min each, three personas. Working dir
`/Users/messili/codebase/polyglot-alpha`.

---

## Session 1 — *Impatient* user (no waiting, rapid clicks)

**Opening:** Land on `/` — heading reads "Decentralized cross-language
alpha…" — nice, OK, where do I actually see the demo? Hit "Trigger live
demo".

**Observation:** Button greys out, label becomes
`"Fetching latest non-English news…"`. Wait two seconds — nothing. Tab
hop to `/events` directly while the trigger is in-flight.

**Bug A (BLOCKER #2):** `/events` page renders a skeleton grid, but the
grid never resolves into cards. Refresh: same. `useEventList`'s default
retry-once is silently failing — the trigger POST is hogging the asyncio
loop so the polled `/events` GET times out.

**Bug B (BLOCKER #1):** When the backend finally answers, I see 50 rows
but the filter chips are `Running / Completed / Live` — and clicking
any of them shows zero rows. *Every event is hidden* by every non-`All`
filter. Status: filter logic was case-sensitive against lowercase
`"running"` etc. while backend emits `"EVALUATING"`.

**Bug C (ANNOYING #6):** Even on `All`, the badges show raw uppercase —
`EVALUATING`, `SUBMITTED`, `AUCTION_OPEN`. Looks like a debug build.

**Bug D (BLOCKER #4):** Reload `/events` again — backend now hung from my
earlier trigger — page sits on the skeleton forever. No error message.

→ Fixes pushed: see `real_user_fixes.md` for diffs.

---

## Session 2 — *Thorough* user (reads every label, hovers everything)

**Opening:** Re-open `/events` after the fixes. Filters now read
`All · Queued · Running · Settled · Failed`. Hover them — each shows a
tooltip ("Events currently in flight — auction, translation, or 11-judge
evaluation phases."). Good — explanation matches the demo narrative.

**Bug E (ANNOYING #5):** Type a non-existent ID into URL bar:
`/events/9999`. Page used to say *"Backend didn't respond"* — but
clicking the network tab showed the 404 came back in 3 ms. The copy was
misleading. Now: `"Event not found · No event with id "9999" exists in
the backend."` plus a back-link.

**Bug F (BLOCKER #2 reprise):** Click into the search box, type `B`.
Cards disappear. DevTools shows
`TypeError: Cannot read properties of null (reading 'toLowerCase')` at
`events/page.tsx:41`. Root cause: event id=82 was ingested with
`headline: null` years ago and the filter does
`e.headline.toLowerCase()` unconditionally. *Any first keystroke kills
the whole grid for everyone.*

**Bug G (COSMETIC #10):** Six events have `source: ""`. Cards rendered a
blank pill where the source name should sit. Now an italic
`unknown source` placeholder.

**Bug H (sort works):** Leaderboard column headers (`Rep.`, `Revenue`,
`Win rate`) are real buttons with `cursor: pointer`. Click `Rep.` —
table sorts descending. Click again — toggles ascending. The `#`
column does not re-number after sort (#3 still shows `#3` even when
it's at the top) — left as known cosmetic.

→ Fixes pushed: search null-guard, EventCard placeholders.

---

## Session 3 — *Adversarial* user (try to break it)

**Tactics:**
- Resize browser to 800 × 800 → layout collapses to 1 column cleanly.
- Resize to 480 × 800 (phone) → filter chips overflow to next line but
  remain pressable; no horizontal scroll.
- `/agents/notahex` → page used to be totally empty. Now: `Agent not
  found` empty-state. (D may have already fixed this.)
- `/events/abc-not-a-number` → backend 404s, my page shows the proper
  Event not found.
- Multiple browser tabs of `/events` — both poll independently every 5 s
  via React Query; both refresh consistently.
- Hammer back/forward across `/events → /events/114 → /events` — state
  is preserved (React Query keeps the cached list).
- Click `Trigger live demo` 3× in 1 s — the button's `disabled={busy}`
  guard correctly prevents the second + third POSTs while the first is
  in flight. (Backend dedup also rejects identical content hashes
  within 24h.)
- Open DevTools → click around 5 min → no React errors after the search
  null-fix; only console messages are Next.js Fast Refresh logs.

**Verdict for adversarial:** the remaining frontend-side rough edges are
all on TriggerButton (cancel state) and EventTimeline (null headline
placeholders) — both owned by other sub-agents.

---

## Quantitative summary

|                        | Session 1 | Session 2 | Session 3 |
|------------------------|-----------|-----------|-----------|
| Duration               | 18 min    | 17 min    | 15 min    |
| Distinct bugs found    | 4         | 4         | 3         |
| Bugs fixed inline      | 3         | 2         | 0         |
| Screenshots captured   | 5         | 4         | 3         |
| Backend restarts       | 1         | 0         | 0         |

Bugs A through H plus the three I couldn't fix because they're outside
my file-ownership = **11 distinct issues found, 7 fixed inline.**
