# README v3 → v4 — Change Log

**Date:** 2026-05-26
**v3:** 653 lines · 6284 words
**v4:** 699 lines · 7162 words
**Delta:** +46 lines · +878 words (well under the +150-line / 800-line budget)

The v4 pass incorporates the overnight 14-sub-agent stress loop findings
([`outputs/MASTER_REPORT.md`](./MASTER_REPORT.md), [`outputs/BUG_BACKLOG.md`](./BUG_BACKLOG.md))
while preserving v3's Medium-quality voice.

---

## What changed

### 1. Badges (top of README)

- Tests badge: `149 Py + 30 Sol + 15 FE` → **`219 Py + 36 Jest + 30 Foundry`** (real counts post-overnight)
- Slither badge: clarifies "0 High / 0 Medium" applies to **first-party** code (OZ `Math.sol` library noise excluded)
- **New Smoke badge:** `Smoke 10/12 GREEN` linking to MASTER_REPORT.md
- Tests + Slither badges now point at `MASTER_REPORT.md` instead of `final_audit_summary.md` (more current)

### 2. TL;DR paragraph 3

Added: builder code `0xa934…beb1` registered, Alchemy Polygon RPC bound,
overnight stress loop reference, real-stack coverage figure (~85%),
smoke 10/12 GREEN.

### 3. Real vs Mock: Honest Accounting

- Phase 1 chain glue + dispatch rows: `PHASE 1 (landing)` → **`REAL (smoke verified)`**
- New row: Alchemy Polygon RPC binding marked REAL with median latency
- Coverage estimate: **25–30% → ~85%** with provenance (Phase 1 + overnight verification)
- WARNING callout rewritten: the gap now is BLEU/COMET reference-lookup wiring
  (HIGH-1 in BUG_BACKLOG), not chain glue. MQM (the most informative of the three)
  is real.

### 4. The Numbers table

Replaced "target 60–75 s" with **measured** values from `perf_benchmark.md`:

| Row | v3 | v4 |
|-----|-----|-----|
| Lifecycle wall clock | 60–75 s target | p50 **65.87 s** measured, p95 ≥180 s on stalls |
| API p95 | — | **8.7 – 29.3 ms** (`/events`, `/leaderboard`, `/events/{id}`) |
| Backend cold start | — | **1.65 s** + Next.js FCP 90–760 ms |
| FAISS lookup median | — | **16.07 ms** vs 100 ms budget |
| Arc RPC eth_blockNumber | — | p50 **590.6 ms** · p95 **828.3 ms** |
| Test suites total | — | **285 pass** (219 + 36 + 30) |
| Slither verdict | post-hardening | post-hardening **first-party** clarified |

### 5. NEW SECTION — "Stress Tested Overnight (2026-05-26, 04:30–08:00 SGT)"

**Position:** before "Audit + Hardening Pass" (chronologically the newer event).

Covers:
- 14 sub-agents launched in 3 waves over 3.5 hours
- 600+ check items across 9 domains
- 47 bugs catalogued, 27 auto-fixed
- Before/after table on 9 surfaces (smoke 4→10/12, mobile 47%→81%, etc.)
- 121 screenshots produced
- **Demo readiness verdict: GREEN mechanism / YELLOW market** with explanation
- Pointers to MASTER_REPORT.md and BUG_BACKLOG.md

Voice: "we put it through a stress test loop" — not boastful, just earned.

### 6. Audit + Hardening Pass

One-line transition update: "Before the overnight stress loop, an earlier
8-audit parallel pass ran…" — chronology now reads cleanly.

### 7. Roadmap

Added a new "Production hardening" phase (1–4 weeks post-ship) capturing
the three concrete Agent H production recommendations:
- `BackgroundTasks` migration for `/trigger/event` (BLOCKER)
- LLM timeout + circuit breaker around the 4-provider fan-out
- `gunicorn --workers 4` + reverse proxy
- BLEU/COMET reference-lookup wiring (HIGH-1)
- Firefox SSE CORS fix (HIGH-2)

Plus a follow-up paragraph explaining what each is, what surfaced it
(Agent H perf benchmark), and that they are roughly day-of-work fixes,
not architecture changes.

### 8. Arc capabilities table

Added Alchemy Polygon RPC row with app id `ngx37mo60qae6ror` and median latency.

### 9. "The Numbers" intro polish

Opener now lists the concrete corpus + event + bid + submission + test counts
that back the "real data, not just simulated" claim.

---

## What stayed the same (deliberate)

- 6 Mermaid diagrams (none added, none removed)
- All 24 unique §5.X cross-reference anchors still resolve
- Section order unchanged except the new "Stress Tested Overnight" insertion
- Voice / tone preserved from v3 (Medium-quality narrative)
- §5.30 honest-scope discipline maintained: no claim about proof-of-market
- Mechanism design defaults table (locked parameters) untouched
- License + Closing Thesis untouched

---

## Anchor-resolve verification

Unique §5.X anchors in v4 (24 distinct):
50, 502, 503, 510, 515, 518, 521, 522, 527, 528, 530, 540, 5402, 541, 542,
543, 544, 546, 547, 548, 55, 551, 56, 57

All identical to v3 anchor set. No anchors added or dropped.
