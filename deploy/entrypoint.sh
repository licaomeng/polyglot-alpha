#!/usr/bin/env bash
# =============================================================================
# PolyglotAlpha — container entrypoint (W23, Hugging Face Spaces)
# =============================================================================
# Boots three processes inside one HF Spaces container:
#   1. uvicorn (FastAPI, 127.0.0.1:8000) — the backend
#   2. next start (Next.js prod, 127.0.0.1:3001) — the UI
#   3. nginx (0.0.0.0:7860) — the only port HF exposes externally
#
# A few practical concerns the script handles:
#   * If the Space has persistent storage mounted at `/data`, copy the
#     seeded SQLite DB there on first boot and re-point DATABASE_URL.
#   * Wait for backend health before firing the pre-seed events so the
#     leaderboard / events list aren't empty on the reviewer's first visit.
#   * Trap SIGTERM/SIGINT so HF's graceful shutdown actually kills children
#     (the default `wait` keeps the parent alive but leaves zombies if we
#     don't propagate the signal).
# -----------------------------------------------------------------------------
set -euo pipefail

log() {
    printf '[%s entrypoint] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

# -----------------------------------------------------------------------------
# Persistent storage handling — HF Spaces "small persistent storage" tier
# mounts a writable /data volume that survives sleeps + redeploys. Without
# it, the SQLite DB lives in the container FS and resets on every redeploy
# (acceptable for a pure demo; cold starts still pre-seed below).
# -----------------------------------------------------------------------------
if [[ -d /data && -w /data ]]; then
    log "persistent /data detected — relocating SQLite DB"
    if [[ ! -f /data/polyglot_alpha.db ]]; then
        if [[ -f /app/polyglot_alpha.db ]]; then
            cp /app/polyglot_alpha.db /data/polyglot_alpha.db
            log "seeded /data/polyglot_alpha.db from container image"
        fi
    fi
    export DATABASE_URL="sqlite:////data/polyglot_alpha.db"
    log "DATABASE_URL=${DATABASE_URL}"
else
    log "no /data volume — using in-image SQLite (will reset on redeploy)"
fi

# -----------------------------------------------------------------------------
# 1. Backend
# -----------------------------------------------------------------------------
cd /app
log "starting uvicorn on 127.0.0.1:8000"
python -m uvicorn polyglot_alpha.api.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --log-level info \
    --no-access-log &
BACKEND_PID=$!

# -----------------------------------------------------------------------------
# 2. Frontend
# -----------------------------------------------------------------------------
log "starting Next.js on 127.0.0.1:3001"
(
    cd /app/ui
    exec npx --no-install next start -H 127.0.0.1 -p 3001
) &
FRONTEND_PID=$!

# -----------------------------------------------------------------------------
# Wait for backend health before firing the pre-seed lifecycle. We bound the
# wait at ~60 s so a wedged backend still surfaces (nginx will return 502 on
# /health and HF Spaces will show the failure clearly).
# -----------------------------------------------------------------------------
wait_for_backend() {
    local attempts=0
    while (( attempts < 60 )); do
        if curl -fsS --max-time 2 http://127.0.0.1:8000/health > /dev/null 2>&1; then
            log "backend healthy after ${attempts}s"
            return 0
        fi
        sleep 1
        attempts=$((attempts + 1))
    done
    log "backend never reached /health — proceeding anyway"
    return 1
}

# -----------------------------------------------------------------------------
# Pre-seed events — fire 3 mock lifecycles so the leaderboard / events /
# history pages have something to show the first reviewer. We background
# this so nginx can come up immediately. The `|| true` ensures any single
# failure doesn't take down the whole entrypoint.
# -----------------------------------------------------------------------------
preseed_events() {
    wait_for_backend || return 0
    log "pre-seeding 3 mock events"
    local i
    for i in 1 2 3; do
        curl -fsS -X POST http://127.0.0.1:8000/trigger/event \
            -H "Content-Type: application/json" \
            -d '{"mode":"mock"}' > /dev/null 2>&1 || \
            log "pre-seed event ${i} failed (continuing)"
        sleep 4
    done
    log "pre-seed complete"
}
# Run pre-seed in a `disown`-ed subshell so its natural exit (after 3
# events ≈ 14s) does NOT trigger the `wait -n` teardown below. Only the
# 3 critical processes (backend, frontend, nginx) should bring down
# the container when they die.
( preseed_events ) &
disown $!

# -----------------------------------------------------------------------------
# 3. nginx — run in foreground as PID 1's last child so its exit takes down
# the container if proxy config is broken (fail-fast).
# -----------------------------------------------------------------------------
log "starting nginx on 0.0.0.0:7860"
nginx -g 'daemon off;' &
NGINX_PID=$!

# -----------------------------------------------------------------------------
# Signal handling — propagate SIGTERM/SIGINT to all children. HF Spaces
# sends SIGTERM with a 30s grace before SIGKILL.
# -----------------------------------------------------------------------------
shutdown() {
    log "received shutdown signal — forwarding to children"
    kill -TERM "$BACKEND_PID" "$FRONTEND_PID" "$NGINX_PID" 2>/dev/null || true
    wait "$BACKEND_PID" "$FRONTEND_PID" "$NGINX_PID" 2>/dev/null || true
    log "all children exited — bye"
    exit 0
}
trap shutdown SIGTERM SIGINT

# Wait on the 3 critical PIDs explicitly — `wait -n` without args
# would catch ANY background job (including the pre-seed loop), so we
# enumerate just the long-running services. If backend/frontend/nginx
# dies, abort the container so failure surfaces in HF logs.
wait -n "$BACKEND_PID" "$FRONTEND_PID" "$NGINX_PID"
log "a critical child process exited — tearing down the rest"
shutdown
