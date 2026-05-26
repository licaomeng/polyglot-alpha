#!/usr/bin/env bash
# PolyglotAlpha — start the UI in production mode (W16-C).
#
# Why: `pnpm dev` (Next.js dev server) compiles routes on first access, which
# adds ~30s lag the first time the user clicks "Trigger live demo" and
# navigates to /events/{id}. It also double-renders in React strict mode,
# making SSE-heavy pages feel laggy. The production build pre-compiles every
# route and runs in React production mode → instant navigation.
#
# Usage:
#   scripts/run_prod_ui.sh           # build (incremental) + start on :3001
#   PORT=3002 scripts/run_prod_ui.sh # start on a different port
#   SKIP_BUILD=1 scripts/run_prod_ui.sh  # skip build, just (re)start server
#
# Exits non-zero with a clear message if any step fails.

set -u
set -o pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
UI_DIR="${PROJECT_ROOT}/ui"
LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/ui.prod.log"
BUILD_LOG="${LOG_DIR}/ui.prod.build.log"

PORT="${PORT:-3001}"
SKIP_BUILD="${SKIP_BUILD:-0}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-20}"

mkdir -p "${LOG_DIR}"

if [[ ! -d "${UI_DIR}" ]]; then
    echo "FATAL: ui directory not found at ${UI_DIR}" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Step 1: kill any existing next dev / next start process on the target port.
# ---------------------------------------------------------------------------
echo "[1/4] Stopping any existing UI processes on port ${PORT} ..."
# Kill parent dev/start wrappers first; child next-server lingers otherwise.
pkill -9 -f "next dev" 2>/dev/null || true
pkill -9 -f "next start" 2>/dev/null || true
# Then anything still listening on the port.
PIDS_ON_PORT="$(lsof -ti:"${PORT}" 2>/dev/null || true)"
if [[ -n "${PIDS_ON_PORT}" ]]; then
    for pid in ${PIDS_ON_PORT}; do
        # Only kill node processes — leave browser/proxy listeners alone.
        if ps -p "${pid}" -o command= 2>/dev/null | grep -qE "next|node"; then
            kill -9 "${pid}" 2>/dev/null || true
        fi
    done
fi
sleep 1

# ---------------------------------------------------------------------------
# Step 2: build (incremental if .next cache is present).
# ---------------------------------------------------------------------------
if [[ "${SKIP_BUILD}" == "1" ]]; then
    echo "[2/4] SKIP_BUILD=1 — skipping next build."
else
    echo "[2/4] Building (incremental if .next cache is valid) — see ${BUILD_LOG}"
    BUILD_START="$(date +%s)"
    ( cd "${UI_DIR}" && pnpm build ) > "${BUILD_LOG}" 2>&1
    BUILD_EXIT=$?
    BUILD_END="$(date +%s)"
    BUILD_SECS=$((BUILD_END - BUILD_START))
    if [[ ${BUILD_EXIT} -ne 0 ]]; then
        echo "FATAL: pnpm build failed (exit ${BUILD_EXIT}, ${BUILD_SECS}s). Last 40 lines:" >&2
        tail -n 40 "${BUILD_LOG}" >&2
        exit ${BUILD_EXIT}
    fi
    echo "    Build OK in ${BUILD_SECS}s."
fi

# ---------------------------------------------------------------------------
# Step 3: start production server in background.
# ---------------------------------------------------------------------------
echo "[3/4] Starting production server on :${PORT} ..."
( cd "${UI_DIR}" && nohup pnpm start -p "${PORT}" > "${LOG_FILE}" 2>&1 & )

# ---------------------------------------------------------------------------
# Step 4: wait for the server to respond on /.
# ---------------------------------------------------------------------------
echo "[4/4] Waiting for http://localhost:${PORT}/ to respond (timeout ${READY_TIMEOUT_S}s) ..."
ELAPSED=0
while [[ ${ELAPSED} -lt ${READY_TIMEOUT_S} ]]; do
    CODE="$(curl -m 2 -o /dev/null -s -w "%{http_code}" "http://localhost:${PORT}/" 2>/dev/null || echo "000")"
    if [[ "${CODE}" == "200" ]]; then
        echo ""
        echo "OK Production UI ready at http://localhost:${PORT}/"
        echo "    logs: ${LOG_FILE}"
        exit 0
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

echo "FATAL: server did not respond with 200 within ${READY_TIMEOUT_S}s. Last 30 lines of log:" >&2
tail -n 30 "${LOG_FILE}" >&2
exit 3
