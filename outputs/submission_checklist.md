# PolyglotAlpha v2 — Submission Checklist (sub-agent R, 2026-05-26)

Wake-time verification for the Agora Agents Hackathon Google Form. Deadline-imminent.

## 1. Form fields — readiness matrix

Form spec verified against `agora-agents-hackathon/README.md` §13 and `submission/README.md` "Submission form quick-fill" table.

| # | Field | Status | Source / value |
|---|-------|--------|----------------|
| 1 | Project Name | READY | `PolyglotAlpha v2` |
| 2 | GitHub Handle | READY | `licaomeng` |
| 3 | One-line pitch | READY | `submission/README.md` tagline (§"Tagline") |
| 4 | Problem statement | READY | `submission/qa.md` Q1 |
| 5 | Project description | READY | `README.md` (699 lines) + `submission/architecture.md` |
| 6 | Team size | READY | `1 (Solo)` |
| 7 | Team members | READY | `licaomeng` |
| 8 | Track / category | READY | Hook 04 + RFB 03 |
| 9 | Tech stack | READY | `submission/README.md` "Tech stack" |
| 10 | Traction | READY | 5 Arc contracts + 4 LLMs + 11 judges + overnight loop |
| 11 | Source code URL | READY | `https://github.com/licaomeng/polyglot-alpha` (remote exists, only 2 commits pushed) |
| 12 | Live demo URL | **PENDING** | `TODO_VERCEL_URL` — Vercel not yet deployed (no `vercel.json`) |
| 13 | Video demo URL | **PENDING** | `TODO_LOOM_URL` — Loom not recorded; script ready at `submission/demo_script.md` |
| 14 | Arc OSS opt-in | READY | MIT contracts |
| 15 | Arc OSS narrative | READY | `submission/qa.md` Q15 |
| 16 | Contact email | READY | `licaomeng@gmail.com` |

## 2. README final-check (699 lines)

- Title, badges, TL;DR, problem, mechanism, run instructions, architecture diagrams, license, roadmap — ALL PRESENT
- §"Stress Tested Overnight (2026-05-26, 04:30–08:00 SGT)" intact at line 603
- No `TODO`/`FIXME`/`XXX`/`TBD` markers
- No Loom or Vercel placeholder strings — **README itself does NOT carry the demo/video links**; those live only in `submission/README.md` as `TODO_LOOM_URL` / `TODO_VERCEL_URL`. Consider adding them to top-of-README once recorded.

## 3. Secrets scan — clean

- `.env` and `.env.local` correctly gitignored
- No `sk-ant-api03-…`, `sk-or-v1-…`, raw private keys in tracked files
- Only `OPENROUTER_API_KEY` *name* references (env var reads in `polyglot_alpha/llm.py`, judges, scripts) — no values
- All `0x…` hits in `corpus/full/polymarket_all_markets_sample.csv` are public Polymarket question/wallet hashes, not secrets
- Note: `outputs/final_audit_summary.md:79` reminds operator to rotate 4 keys (`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `HACKATHON_WALLET_PRIVATE_KEY`) — historical, not currently exposed

## 4. Git state

- Remote: `https://github.com/licaomeng/polyglot-alpha.git`
- Only **2 commits pushed** (`d96b815`, `ce264ff`) — entire current README, contracts, UI, tests, outputs are **uncommitted** (167 unstaged paths). User must commit + push before 8 AM.

## 5. Five must-do manual actions before 8 AM SGT

| # | Action | One-liner |
|---|--------|-----------|
| 1 | Commit + push code | `cd /Users/messili/codebase/polyglot-alpha && git add -A && git commit -m "feat: PolyglotAlpha v2 hackathon submission" && git push origin main` |
| 2 | Deploy UI to Vercel | `cd /Users/messili/codebase/polyglot-alpha/ui && npx vercel --prod` (then capture URL into `submission/README.md`) |
| 3 | Record Loom (~3 min) | Open `submission/demo_script.md`, follow blocks A–G, upload unlisted-shareable at `https://www.loom.com/new` |
| 4 | Fill Google Form | Copy fields from `submission/README.md` "Submission form quick-fill" table; paste Vercel + Loom URLs |
| 5 | Accept HF COMET-kiwi license | Visit `https://huggingface.co/Unbabel/wmt22-cometkiwi-da` and click "Agree" (only if COMET judge will run in prod; demo uses BLEU-MQM fallback) |

## 6. Blockers found

- None code-side. **Two operational blockers remain**, both in user's hands: Vercel URL + Loom URL.
- Recommend updating `README.md` top section to surface the demo and video URLs once available, mirroring `submission/README.md` "Links" table.
