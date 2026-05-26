# =============================================================================
# PolyglotAlpha — Hugging Face Spaces deploy image (W23, mock-only)
# =============================================================================
# Single container that ships the FastAPI backend + Next.js production build
# behind a tiny nginx reverse proxy on port 7860 (HF Spaces only exposes one
# port externally). DISABLE_LIVE is hard-set so reviewers can't trip an empty
# API key path; the W5 mock-mode fixtures drive the entire demo lifecycle.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Stage 1: build the Next.js production bundle
# -----------------------------------------------------------------------------
FROM node:20-alpine AS ui-build
WORKDIR /app/ui

# Install deps. NOTE: the repo's `package-lock.json` was generated against
# Indeed's internal npm proxy (`npm.artifacts.indeed.tech`) and `npm ci`
# fails with E401 outside that VPN. We deliberately drop the lockfile and
# regenerate it against the public registry so the HF builder (which has
# no Indeed access) succeeds. For a mock-only demo the small version drift
# is acceptable; switch to `npm ci` once a clean public lockfile lands.
# The cache mount lets re-builds reuse the npm metadata + tarball cache
# (~7-10 min cold install drops to <30 s on warm rebuilds).
COPY ui/package.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm install --no-audit --no-fund \
        --registry=https://registry.npmjs.org/ \
        --legacy-peer-deps \
        --prefer-offline

# Copy the rest of the UI source and build. `NEXT_PUBLIC_DISABLE_LIVE` is
# inlined into the client bundle at build time, so this MUST be set here —
# changing it later via runtime env has no effect on the compiled JS.
COPY ui/ ./

# `next.config.mjs` AND `lib/api.ts` both hardcode a `|| "http://localhost:8000"`
# fallback for `NEXT_PUBLIC_API_BASE`, which would break in the browser when
# the page is served from huggingface.co (the fallback would point at the
# reviewer's own laptop, not the HF Space). Patch both to fall back to an
# empty string so api.ts produces same-origin URLs (`/events`,
# `/trigger/event`, ...) that nginx routes to uvicorn :8000.
RUN sed -i 's|process\.env\.NEXT_PUBLIC_API_BASE || "http://localhost:8000"|process.env.NEXT_PUBLIC_API_BASE || ""|' next.config.mjs && \
    sed -i 's|"http://localhost:8000"|""|' lib/api.ts

ENV NEXT_PUBLIC_API_BASE=""
ENV NEXT_PUBLIC_DISABLE_LIVE=true
ENV NODE_ENV=production
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 2: runtime image
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime
WORKDIR /app

# System packages: nginx (reverse proxy), curl (entrypoint pre-seed), nodejs
# (Next.js `next start`). `ca-certificates` keeps outbound HTTPS happy if a
# reviewer pokes a live-mode endpoint we forgot to lock down.
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        curl \
        ca-certificates \
        gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Backend Python deps — install BEFORE copying the app so layer cache survives
# a code edit. We use the pruned mock-only requirements (no torch / comet /
# sentence-transformers) so the HF builder doesn't OOM on the ML wheels.
COPY deploy/requirements-mock.txt /tmp/requirements-mock.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements-mock.txt

# Backend code. `pip install -e .` registers the package on the import path;
# pyproject.toml carries no `dependencies` so this is essentially `develop`
# mode without re-installing anything heavy.
COPY pyproject.toml ./
COPY polyglot_alpha/ ./polyglot_alpha/
COPY scripts/ ./scripts/
COPY contracts/out/ ./contracts/out/
# NOTE: the top-level `corpus/` directory (faiss index, polymarket dumps,
# 1.1 GB of training data) is intentionally NOT copied — it's only consumed
# by the live RAG / pattern-analysis paths which are dead code in
# DISABLE_LIVE mode. The bundled few-shot exemplars live in the
# `polyglot_alpha.corpus` PYTHON package and ship via the polyglot_alpha
# copy above.
RUN pip install --no-cache-dir --no-deps -e .

# Frontend artifact — copy the .next build + node_modules from stage 1. We
# pull node_modules whole because `next start` needs the runtime deps to
# resolve at request time (Next 15 + react-server bundling).
COPY --from=ui-build /app/ui/.next ./ui/.next
COPY --from=ui-build /app/ui/public ./ui/public
COPY --from=ui-build /app/ui/package.json ./ui/package.json
COPY --from=ui-build /app/ui/node_modules ./ui/node_modules
COPY --from=ui-build /app/ui/next.config.mjs ./ui/next.config.mjs

# Pre-seed SQLite with empty tables + the few-shot exemplars so the first
# reviewer click doesn't hit an empty `events` / `few_shot_exemplars` table.
# `init_db()` is idempotent; the ingest script is too (skips on conflict).
RUN python -c "from polyglot_alpha.persistence import init_db; init_db()" \
    && (python scripts/ingest_few_shots.py || true)

# Reverse-proxy + process supervisor configs.
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Runtime env: lock the deploy to mock-only and keep concurrency tame so the
# 2 vCPU HF free tier doesn't thrash under a small audience.
ENV DISABLE_LIVE=true
ENV DEFAULT_EVENT_MODE=mock
ENV LLM_BACKEND=mock
ENV LIFECYCLE_MAX_CONCURRENCY=2
ENV PYTHONUNBUFFERED=1
ENV PORT=7860
# DB lives next to the app by default. If HF persistent storage is enabled
# in the Space settings, the entrypoint moves the seeded DB to `/data` and
# overrides `DATABASE_URL` at boot so the events table survives sleeps.
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 7860

CMD ["/usr/local/bin/entrypoint.sh"]
