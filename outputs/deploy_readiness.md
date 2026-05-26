# PolyglotAlpha v2 — Deploy Readiness Audit (sub-agent Q)

_Date: 2026-05-26 — pre-submission audit, NO commits, NO deploys performed._

## 1. Frontend (Next.js → Vercel)

### Build result

- **Status**: PASS
- **Tool**: `pnpm build` (Next.js 15.5.18)
- **Compile time**: 8.5s
- **Lint + typecheck**: passed (no warnings)
- **Static pages generated**: 8/8
- **Build artifact size**: `.next/static` 1.7 MB, `.next/server` 1.9 MB (full `.next` w/ cache: 274 MB — Vercel only ships static+server)

### Route sizes (First Load JS)

| Route | Size | First Load |
|---|---|---|
| `/` | 6.15 kB | 132 kB |
| `/about` | 124 B | 103 kB |
| `/agents/[address]` (ƒ) | 6.76 kB | 129 kB |
| `/events` | 2.61 kB | 129 kB |
| `/events/[id]` (ƒ) | **205 kB** | **328 kB** ← biggest (xyflow + recharts) |
| `/history` | 2.22 kB | 129 kB |
| `/leaderboard` | 5.5 kB | 128 kB |
| shared chunks | — | 103 kB |

All within Vercel limits. `/events/[id]` is the heaviest route — acceptable, no action required.

### Vercel config

- **No `vercel.json` present.** Auto-detection from `package.json` will pick the Next.js preset — fine, no action needed.
- **Framework**: Next.js (auto-detected).
- **Root dir for Vercel project**: `ui/`.
- **Install command**: `pnpm install` (or `npm install`, lockfile present).
- **Build command**: `next build` (default).
- **Output**: `.next` (default).

### Required prod ENV vars (frontend)

| Var | Value | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `https://<backend-host>` | **Required.** Defaults to `http://localhost:8000` — must be set on Vercel or the deployed UI will fail. |

### Hardcoded localhost references (5 hits)

All are user-facing error strings / footer display — none break functionality because all real fetches go through `lib/api.ts` which reads `NEXT_PUBLIC_API_BASE`:

- `ui/lib/api.ts:14` — default fallback (correct, env-overridable).
- `ui/app/events/page.tsx:95` — error copy ("Couldn't reach the backend at http://localhost:8000").
- `ui/app/events/[id]/page.tsx:76` — error copy.
- `ui/components/shared/SiteFooter.tsx:14` — footer displays the API base (uses `process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"` — env-overridable).

**Recommended cleanup (non-blocking)**: replace hardcoded "http://localhost:8000" in error copy strings with `NEXT_PUBLIC_API_BASE` so the message reflects the actual misconfigured target. Not required for deploy.

## 2. Backend (FastAPI) deploy plan

### Source structure

- **Entrypoint**: `polyglot_alpha.api.main:app` (FastAPI factory `create_app()`).
- **No `Dockerfile` present.** No `requirements.txt`. `pyproject.toml` has metadata only — **dependencies are NOT declared in pyproject** (currently relies on whatever is in the dev `.venv`). This is a **blocker for clean deploys** — a `requirements.txt` or proper `[project.dependencies]` must be added.
- **DB**: SQLite at `./polyglot_alpha.db` by default; override via `DATABASE_URL` (Postgres supported via SQLModel/SQLAlchemy).
- **PubSub**: in-memory by default; Redis if `REDIS_URL` is set (needed for multi-worker SSE).

### Required prod ENV vars (backend)

**Core / API**

- `OPENROUTER_API_KEY` (LLM provider — required)
- `GEMINI_API_KEY` or `GOOGLE_API_KEY` (optional fallback)
- `DEEPSEEK_API_KEY` (optional — d5 judge)
- `OPENROUTER_MODEL`, `GEMINI_MODEL` (optional overrides)
- `CORS_ORIGINS` — comma-separated list (must include the Vercel URL, no `*`)
- `DATABASE_URL` — Postgres URL strongly recommended for prod
- `REDIS_URL`, `REDIS_CHANNEL` — required if running >1 worker

**On-chain (Arc)**

- `ARC_TESTNET_RPC`, `ARC_CHAIN_ID` (default 5042002)
- `TRANSLATION_AUCTION_ADDRESS`
- `REPUTATION_REGISTRY_ADDRESS`
- `QUESTION_REGISTRY_ADDRESS`
- `BUILDER_FEE_ROUTER_ADDRESS`
- `USDC_ADDRESS`
- `HACKATHON_WALLET_PRIVATE_KEY` (operator) and per-agent `*_PRIVATE_KEY` (see `wallets.py`)
- `OPERATOR_WALLET_PRIVATE_KEY` (ingestion dispatcher)

**Polymarket**

- `POLYMARKET_BUILDER_API_KEY`, `POLYMARKET_BUILDER_CODE`, `POLYMARKET_BUILDER_NAME`
- `POLYMARKET_MODE`, `POLYMARKET_REAL_DAILY_LIMIT`, `POLYMARKET_REAL_QUALITY_GATE`
- `POLYGON_RPC`, `CTF_EXCHANGE_V2_ADDRESS`
- `POLYGLOT_BUILDER_REGISTRY_PATH`

**Tuning / mode flags**

- `AUCTION_WINDOW_SECONDS` (60), `DEFAULT_STAKE_USDC` (5), `QUALITY_PASS_THRESHOLD` (0.7)
- `AUCTION_MODE` (real/mock), `POLYGLOT_DEMO_MODE`
- `PANEL_TIMEOUT_SECONDS` (120), `PER_JUDGE_TIMEOUT_S` (60)

No `ALCHEMY_API_KEY` reference found in source (Q's brief mentioned it — not used by current code).

### Cold start estimate

- **Bare FastAPI** (no judging): `/health` p50 = **1.66 ms** (from `perf_benchmark.md`). Process startup ~2-4 s including `init_db` and SQLite WAL setup.
- **SentenceTransformer load** (`d8_duplicate_detection`, `corpus/embed`, `corpus/lookup`): **lazy** — only on first judge invocation. Cold first-judge call adds **60+ seconds** per `judges/style_alignment/d8_duplicate_detection.py:127` comment.
- **COMET (`Unbabel/wmt22-cometkiwi-da`) download + load**: lazy, first-call cost on a 16-GB VM is typically **30-90 s** + ~2.3 GB model download from HF on cold disk.
- **Net first-event lifecycle cold start**: expect **2-4 min** on first request after deploy; subsequent requests are warm (<5 s panel timing).

### Recommended deploy target

**Primary recommendation: Fly.io** (or Modal for the ML-heavy panel).

Rationale:

1. **Persistent disk + ≥2 vCPU / 4-8 GB RAM** required for COMET + SentenceTransformer caches. Vercel functions and Cloudflare Workers are non-starters (memory + cold start + binary deps).
2. **Long-running SSE** (`/events`) — needs a stateful HTTP server. Fly.io VMs / Render web service / Railway all support SSE; serverless does not.
3. **HF model cache should be a mounted volume** (`/data/.cache/huggingface`) to avoid re-downloading on every restart.

Comparison:

| Host | Pros | Cons |
|---|---|---|
| **Fly.io** (recommended) | persistent volumes, regions, cheap small VMs, good SSE support | manual Dockerfile required |
| **Modal** | best for ML cold-start (image-cached models, GPU optional) | newer DX, SSE pattern less mature |
| **Render** | simple, supports persistent disk on paid plan | slower deploys, no free SSE on starter tier |
| **Railway** | easy DX, env-var ergonomics | persistent storage costs more |
| **DO App Platform** | predictable pricing | larger images slow to deploy |

### Pre-deploy blockers / TODO

1. **Add `Dockerfile`** — base `python:3.11-slim`, install `build-essential` for torch wheels, mount `/data` volume for HF cache.
2. **Add `requirements.txt`** (or move deps into `[project.dependencies]` in `pyproject.toml`). Currently nothing pins fastapi/uvicorn/torch/comet/etc.
3. **Migrate `DATABASE_URL` to Postgres** before prod (SQLite WAL won't survive horizontal scale; works fine on a single Fly VM as long as the DB file lives on the volume).
4. **Set `CORS_ORIGINS`** to the exact Vercel URL — wildcard is silently dropped by the app.
5. **Pre-warm HF cache in image build**: in Dockerfile, run a tiny `python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"` + COMET `download_model` at build time so the first user request isn't a 90-second wait.

## 3. Post-deploy smoke checks

Run after first successful deploy. Replace `$API` with the backend URL.

```bash
export API=https://polyglot-alpha-api.fly.dev

# 1. Liveness — should return {"status":"ok"} in <100 ms
curl -fsS "$API/health" | jq .

# 2. Root metadata
curl -fsS "$API/" | jq .

# 3. Events list (should be array, may be empty on cold DB)
curl -fsS "$API/events" | jq 'length, .[0]'

# 4. Leaderboard (should return rows or empty list, no 500)
curl -fsS "$API/leaderboard" | jq 'length, .[0]'

# 5. SSE stream — confirm headers + receive first heartbeat within 10 s
curl -N -fsS --max-time 10 -H "Accept: text/event-stream" "$API/events/stream" || true
```

Frontend smoke (replace `$UI` with Vercel URL):

```bash
export UI=https://polyglot-alpha.vercel.app
curl -fsS "$UI" -o /dev/null -w "home: %{http_code} %{time_total}s\n"
curl -fsS "$UI/events" -o /dev/null -w "events: %{http_code} %{time_total}s\n"
curl -fsS "$UI/leaderboard" -o /dev/null -w "leaderboard: %{http_code} %{time_total}s\n"
```

## Summary

| Area | Status |
|---|---|
| Next.js build | **PASS** (8.5 s, 1.7 MB static) |
| Vercel readiness | Ready; needs `NEXT_PUBLIC_API_BASE` env var |
| Backend Dockerfile | **MISSING** — blocker |
| Backend requirements pinning | **MISSING** — blocker |
| ML model cold start | 60-120 s first call (lazy) — recommend pre-warming in image |
| Recommended host | **Fly.io** (or Modal for ML-first) |
