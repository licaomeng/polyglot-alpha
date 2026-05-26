#!/usr/bin/env bash
# PolyglotAlpha demo readiness dashboard.
#
# Read-only health check.  Runs ~20 subsystem probes and prints one line per
# check.  Exit code: 0 = all READY, 1 = any WARN (and no FAIL), 2 = any FAIL.
#
# Usage:
#   scripts/demo_readiness.sh
#
# Env overrides (optional):
#   BACKEND_URL   default http://localhost:8000
#   UI_URL        default http://localhost:3001 (falls back to 3000 if 3001 unreachable)
#   ARC_RPC       default value from .env (ARC_TESTNET_RPC)
#   DB_PATH       default polyglot_alpha.db
#   PY            default ./.venv/bin/python

set -u
set -o pipefail

# ---------------------------------------------------------------------------
# Locate project root (script may be invoked from anywhere).
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# ---------------------------------------------------------------------------
# Color handling.  Degrade gracefully when stdout is not a TTY.
# ---------------------------------------------------------------------------
if [[ -t 1 ]] && command -v tput >/dev/null 2>&1; then
    C_GREEN="$(tput setaf 2 2>/dev/null || true)"
    C_YELLOW="$(tput setaf 3 2>/dev/null || true)"
    C_RED="$(tput setaf 1 2>/dev/null || true)"
    C_DIM="$(tput dim 2>/dev/null || true)"
    C_BOLD="$(tput bold 2>/dev/null || true)"
    C_RESET="$(tput sgr0 2>/dev/null || true)"
else
    C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""; C_BOLD=""; C_RESET=""
fi

# ---------------------------------------------------------------------------
# Defaults / env.
# ---------------------------------------------------------------------------
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
UI_URL_PREF="${UI_URL:-http://localhost:3001}"
DB_PATH="${DB_PATH:-${PROJECT_ROOT}/polyglot_alpha.db}"
PY="${PY:-${PROJECT_ROOT}/.venv/bin/python}"
ENV_FILE="${ENV_FILE:-${PROJECT_ROOT}/.env}"

# Source .env values we need (without polluting shell with everything).
_get_env_var() {
    local key="$1"
    if [[ -f "${ENV_FILE}" ]]; then
        grep -E "^${key}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*$//'
    fi
}

ARC_RPC="${ARC_RPC:-$(_get_env_var ARC_TESTNET_RPC)}"
OPERATOR_ADDR="$(_get_env_var HACKATHON_WALLET_ADDRESS)"
ANTHROPIC_KEY="$(_get_env_var ANTHROPIC_API_KEY)"
ALPHA_PK="$(_get_env_var ALPHA_WALLET_PRIVATE_KEY)"
BRAVO_PK="$(_get_env_var BRAVO_WALLET_PRIVATE_KEY)"
CHARLIE_PK="$(_get_env_var CHARLIE_WALLET_PRIVATE_KEY)"

# Counters.
READY_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

# ---------------------------------------------------------------------------
# Reporting helper.  status=READY|WARN|FAIL.
# ---------------------------------------------------------------------------
report() {
    local status="$1" name="$2" detail="$3"
    local tag color
    case "${status}" in
        READY) tag="[READY]"; color="${C_GREEN}"; READY_COUNT=$((READY_COUNT + 1)) ;;
        WARN)  tag="[WARN] "; color="${C_YELLOW}"; WARN_COUNT=$((WARN_COUNT + 1)) ;;
        FAIL)  tag="[FAIL] "; color="${C_RED}";   FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
        *)     tag="[????]"; color="" ;;
    esac
    # Pad name to 22 chars for alignment.
    printf "  %s%s%s %-22s %s%s%s\n" \
        "${color}" "${tag}" "${C_RESET}" \
        "${name}" "${C_DIM}" "${detail}" "${C_RESET}"
}

section() {
    printf "\n%s%s%s\n" "${C_BOLD}" "$1" "${C_RESET}"
}

# ---------------------------------------------------------------------------
# Curl wrapper that always returns code + body even on timeout.
# ---------------------------------------------------------------------------
http_status() {
    # $1=url, $2=timeout (default 3).  Always echoes exactly one HTTP code
    # (or 000 on unreachable / timeout) — never concatenates the two.
    local url="$1" timeout="${2:-3}"
    local code
    code=$(curl -sS -o /dev/null -m "${timeout}" -w "%{http_code}" "${url}" 2>/dev/null)
    echo "${code:-000}"
}

http_status_post_json() {
    # $1=url, $2=json body, $3=timeout.  Always echoes one code.
    local url="$1" body="$2" timeout="${3:-5}"
    local code
    code=$(curl -sS -o /dev/null -m "${timeout}" -w "%{http_code}" \
        -H "Content-Type: application/json" -X POST -d "${body}" "${url}" 2>/dev/null)
    echo "${code:-000}"
}

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
LINE="═══════════════════════════════════════════════════════════════════"
printf "%sPolyglotAlpha demo readiness — %s%s\n" "${C_BOLD}" "${NOW}" "${C_RESET}"
printf "%s\n" "${LINE}"

# ---------------------------------------------------------------------------
# INFRASTRUCTURE
# ---------------------------------------------------------------------------
section "INFRASTRUCTURE"

# 1) Backend /health
T0_NS=$(date +%s%N 2>/dev/null || echo 0)
BACKEND_CODE="$(curl -sS -o /tmp/_dr_health.$$ -m 3 -w '%{http_code} %{time_total}' "${BACKEND_URL}/health" 2>/dev/null || echo '000 0')"
BACKEND_STATUS="${BACKEND_CODE%% *}"
BACKEND_LATENCY="${BACKEND_CODE##* }"
if [[ "${BACKEND_STATUS}" == "200" ]]; then
    LAT_MS=$(awk -v t="${BACKEND_LATENCY}" 'BEGIN{printf "%d", t*1000}')
    report READY "backend" "/health 200 (latency ${LAT_MS}ms)"
    BACKEND_OK=1
else
    report FAIL "backend" "/health unreachable (HTTP ${BACKEND_STATUS}) — start: uvicorn polyglot_alpha.api.main:app --port 8000"
    BACKEND_OK=0
fi
rm -f /tmp/_dr_health.$$

# 2) UI — try preferred URL, then fall back to :3000.  Use 8s timeout
# because Next.js dev server may stall briefly on first request.
UI_URL="${UI_URL_PREF}"
UI_STATUS="$(http_status "${UI_URL}" 8)"
if [[ "${UI_STATUS}" != "200" ]]; then
    ALT_URL="http://localhost:3000"
    ALT_STATUS="$(http_status "${ALT_URL}" 8)"
    if [[ "${ALT_STATUS}" == "200" ]]; then
        # Verify it's actually polyglot UI (not boxxo/other).
        TITLE="$(curl -sS -m 5 "${ALT_URL}" 2>/dev/null | grep -oE '<title>[^<]*</title>' | head -1)"
        if [[ "${TITLE}" == *"Polyglot"* || "${TITLE}" == *"polyglot"* ]]; then
            UI_URL="${ALT_URL}"
            UI_STATUS="${ALT_STATUS}"
            report READY "ui" "${UI_URL}/ 200 (fallback port)"
        else
            report WARN "ui" "${UI_URL_PREF} unreachable; ${ALT_URL} responds but title=${TITLE:-?} (not polyglot)"
            UI_URL=""
        fi
    else
        report FAIL "ui" "${UI_URL_PREF}/ unreachable (HTTP ${UI_STATUS}) — start: (cd ui && npm run dev)"
        UI_URL=""
    fi
else
    report READY "ui" "${UI_URL}/ 200"
fi

# 3) SQLite file
if [[ -r "${DB_PATH}" ]]; then
    DB_SIZE=$(stat -f%z "${DB_PATH}" 2>/dev/null || stat -c%s "${DB_PATH}" 2>/dev/null || echo 0)
    DB_SIZE_MB=$(awk -v b="${DB_SIZE}" 'BEGIN{printf "%.1f", b/1048576}')
    report READY "sqlite" "$(basename "${DB_PATH}") readable (${DB_SIZE_MB} MB)"
    DB_OK=1
else
    report FAIL "sqlite" "${DB_PATH} not readable"
    DB_OK=0
fi

# 4) Required env vars
MISSING_REQUIRED=()
OPTIONAL_MISSING=()
[[ -z "${ANTHROPIC_KEY}" ]] && MISSING_REQUIRED+=("ANTHROPIC_API_KEY")
[[ -z "${ARC_RPC}" ]] && MISSING_REQUIRED+=("ARC_TESTNET_RPC")
# OPERATOR_*_KEY treated as seeder PKs per .env.example naming convention.
[[ -z "${ALPHA_PK}" ]] && OPTIONAL_MISSING+=("ALPHA_WALLET_PRIVATE_KEY")
[[ -z "${BRAVO_PK}" ]] && OPTIONAL_MISSING+=("BRAVO_WALLET_PRIVATE_KEY")
[[ -z "${CHARLIE_PK}" ]] && OPTIONAL_MISSING+=("CHARLIE_WALLET_PRIVATE_KEY")
if [[ ${#MISSING_REQUIRED[@]} -gt 0 ]]; then
    report FAIL "env vars" "missing required: ${MISSING_REQUIRED[*]}"
elif [[ ${#OPTIONAL_MISSING[@]} -gt 0 ]]; then
    report WARN "env vars" "seeder PKs missing (${#OPTIONAL_MISSING[@]}/3) — live mode partial, mock-only OK"
else
    report READY "env vars" "all required + seeder PKs present"
fi

# 5) Arc RPC reachable.
if [[ -n "${ARC_RPC}" ]]; then
    RPC_RESP=$(curl -sS -m 5 -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
        "${ARC_RPC}" 2>/dev/null || echo "")
    BLOCK_HEX=$(echo "${RPC_RESP}" | jq -r '.result // empty' 2>/dev/null || echo "")
    if [[ -n "${BLOCK_HEX}" && "${BLOCK_HEX}" =~ ^0x ]]; then
        BLOCK_DEC=$(printf "%d" "${BLOCK_HEX}" 2>/dev/null || echo 0)
        if (( BLOCK_DEC > 0 )); then
            report READY "arc rpc" "block ${BLOCK_DEC}"
        else
            report FAIL "arc rpc" "block_number=0 — chain stalled?"
        fi
    else
        report FAIL "arc rpc" "${ARC_RPC} unreachable / bad response"
    fi
else
    report FAIL "arc rpc" "ARC_TESTNET_RPC not configured"
fi

# ---------------------------------------------------------------------------
# WALLETS / GAS
# ---------------------------------------------------------------------------
section "WALLETS / GAS"

eth_balance() {
    # echoes ETH balance as a decimal string, or empty on error.
    local addr="$1"
    [[ -z "${ARC_RPC}" || -z "${addr}" ]] && { echo ""; return; }
    local resp
    resp=$(curl -sS -m 5 -H "Content-Type: application/json" \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_getBalance\",\"params\":[\"${addr}\",\"latest\"],\"id\":1}" \
        "${ARC_RPC}" 2>/dev/null || echo "")
    local hex
    hex=$(echo "${resp}" | jq -r '.result // empty' 2>/dev/null)
    [[ -z "${hex}" || ! "${hex}" =~ ^0x ]] && { echo ""; return; }
    "${PY}" -c "print(int('${hex}', 16) / 1e18)" 2>/dev/null || echo ""
}

# Derive address from a private key using the venv python (web3 has eth_account).
pk_to_address() {
    local pk="$1"
    [[ -z "${pk}" ]] && { echo ""; return; }
    "${PY}" -c "
from eth_account import Account
try:
    pk = '${pk}'
    if not pk.startswith('0x'):
        pk = '0x' + pk
    print(Account.from_key(pk).address)
except Exception:
    pass
" 2>/dev/null
}

# 6) Operator wallet balance.
if [[ -n "${OPERATOR_ADDR}" ]]; then
    OP_BAL=$(eth_balance "${OPERATOR_ADDR}")
    if [[ -n "${OP_BAL}" ]]; then
        OP_BAL_FMT=$(awk -v v="${OP_BAL}" 'BEGIN{printf "%.4f", v}')
        TIER=$(awk -v v="${OP_BAL}" 'BEGIN{ if(v>=0.1)print "READY"; else if(v>=0.01)print "WARN"; else print "FAIL"}')
        report "${TIER}" "operator" "${OP_BAL_FMT} ETH @ ${OPERATOR_ADDR:0:10}…"
    else
        report FAIL "operator" "could not query balance for ${OPERATOR_ADDR}"
    fi
else
    report FAIL "operator" "HACKATHON_WALLET_ADDRESS not configured"
fi

# 7) Seeder PKs configured.
SEEDER_CONFIG_COUNT=0
[[ -n "${ALPHA_PK}" ]] && SEEDER_CONFIG_COUNT=$((SEEDER_CONFIG_COUNT + 1))
[[ -n "${BRAVO_PK}" ]] && SEEDER_CONFIG_COUNT=$((SEEDER_CONFIG_COUNT + 1))
[[ -n "${CHARLIE_PK}" ]] && SEEDER_CONFIG_COUNT=$((SEEDER_CONFIG_COUNT + 1))
if (( SEEDER_CONFIG_COUNT == 3 )); then
    report READY "seeders configured" "3/3 PKs in .env"
elif (( SEEDER_CONFIG_COUNT > 0 )); then
    report WARN "seeders configured" "${SEEDER_CONFIG_COUNT}/3 PKs in .env"
else
    report WARN "seeders configured" "0/3 PKs in .env (mock-only demo)"
fi

# 8) Seeder balances (only if any configured).
if (( SEEDER_CONFIG_COUNT == 0 )); then
    report WARN "seeder balances" "skipped (no PKs configured)"
else
    SEEDER_FAILS=0; SEEDER_WARNS=0; SEEDER_OKS=0
    SEEDER_LINES=""
    for tuple in "Alpha:${ALPHA_PK}" "Bravo:${BRAVO_PK}" "Charlie:${CHARLIE_PK}"; do
        nm="${tuple%%:*}"; pk="${tuple#*:}"
        [[ -z "${pk}" ]] && continue
        addr=$(pk_to_address "${pk}")
        [[ -z "${addr}" ]] && { SEEDER_FAILS=$((SEEDER_FAILS+1)); SEEDER_LINES+="${nm}=bad_pk "; continue; }
        bal=$(eth_balance "${addr}")
        if [[ -z "${bal}" ]]; then
            SEEDER_FAILS=$((SEEDER_FAILS+1)); SEEDER_LINES+="${nm}=err "
        else
            bal_fmt=$(awk -v v="${bal}" 'BEGIN{printf "%.4f", v}')
            tier=$(awk -v v="${bal}" 'BEGIN{ if(v>=0.005)print "READY"; else if(v>=0.001)print "WARN"; else print "FAIL"}')
            case "${tier}" in
                READY) SEEDER_OKS=$((SEEDER_OKS+1));;
                WARN) SEEDER_WARNS=$((SEEDER_WARNS+1));;
                FAIL) SEEDER_FAILS=$((SEEDER_FAILS+1));;
            esac
            SEEDER_LINES+="${nm}=${bal_fmt} "
        fi
    done
    if (( SEEDER_FAILS > 0 )); then
        report FAIL "seeder balances" "${SEEDER_LINES}"
    elif (( SEEDER_WARNS > 0 )); then
        report WARN "seeder balances" "${SEEDER_LINES}"
    else
        report READY "seeder balances" "${SEEDER_LINES}"
    fi
fi

# ---------------------------------------------------------------------------
# DB TABLES POPULATED
# ---------------------------------------------------------------------------
section "DB TABLES POPULATED"

db_count() {
    # $1=table, $2=where clause (optional).  Echoes count, or '-1' on error.
    if (( DB_OK == 0 )); then echo -1; return; fi
    local sql="SELECT COUNT(*) FROM $1"
    if [[ -n "${2:-}" ]]; then sql="${sql} WHERE $2"; fi
    "${PY}" -c "
import sqlite3, sys
try:
    c = sqlite3.connect('${DB_PATH}')
    print(c.execute(\"${sql}\").fetchone()[0])
except Exception:
    print(-1)
" 2>/dev/null
}

db_table_exists() {
    if (( DB_OK == 0 )); then echo 0; return; fi
    "${PY}" -c "
import sqlite3
c = sqlite3.connect('${DB_PATH}')
r = c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='$1'\").fetchone()
print(1 if r else 0)
" 2>/dev/null
}

# 9) events
N=$(db_count events)
if (( N > 0 )); then
    report READY "events" "${N} rows"
elif (( N == 0 )); then
    report WARN "events" "0 rows (no demo history)"
else
    report FAIL "events" "query failed"
fi

# 10) agent_reputation
N=$(db_count agent_reputation)
if (( N > 0 )); then
    report READY "agent_reputation" "${N} rows"
elif (( N == 0 )); then
    report WARN "agent_reputation" "0 rows"
else
    report FAIL "agent_reputation" "query failed"
fi

# 11) few_shot_exemplars
N=$(db_count few_shot_exemplars)
if (( N >= 50 )); then
    report READY "few_shot_exemplars" "${N} rows"
elif (( N >= 0 )); then
    report WARN "few_shot_exemplars" "${N} rows (< 50 expected)"
else
    report FAIL "few_shot_exemplars" "query failed"
fi

# 12) corpus_markets
N=$(db_count corpus_markets)
if (( N >= 10 )); then
    report READY "corpus_markets" "${N} rows"
elif (( N >= 0 )); then
    report WARN "corpus_markets" "${N} rows (< 10 expected)"
else
    report FAIL "corpus_markets" "query failed"
fi

# 13) raw_entries table exists (W13-A).
EX=$(db_table_exists raw_entries)
if [[ "${EX}" == "1" ]]; then
    RAW_N=$(db_count raw_entries)
    if (( RAW_N >= 0 )); then
        report READY "raw_entries" "table exists (${RAW_N} rows)"
    else
        report WARN "raw_entries" "table exists, count failed"
    fi
else
    report FAIL "raw_entries" "table missing (W13-A migration not applied)"
fi

# 14) polymarket_submissions NULL payload should be 0 (W13-A backfill).
NULLN=$(db_count polymarket_submissions "payload IS NULL")
TOTAL=$(db_count polymarket_submissions)
if (( NULLN == 0 )); then
    report READY "polymarket NULL payload" "0/${TOTAL} rows (W13-A backfill clean)"
elif (( NULLN > 0 )); then
    report FAIL "polymarket NULL payload" "${NULLN}/${TOTAL} rows still NULL — re-run backfill_polymarket_payload.py"
else
    report FAIL "polymarket NULL payload" "query failed"
fi

# ---------------------------------------------------------------------------
# MODELS / DATA
# ---------------------------------------------------------------------------
section "MODELS / DATA"

# 15) SBert MiniLM cached. We do a path-existence check (no import — slow).
SBERT_DIRS=(
    "${HF_HOME:-${HOME}/.cache/huggingface}/hub/models--sentence-transformers--all-MiniLM-L6-v2"
    "${HOME}/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2"
)
SBERT_FOUND=""
for d in "${SBERT_DIRS[@]}"; do
    if [[ -d "${d}" ]]; then SBERT_FOUND="${d}"; break; fi
done
if [[ -n "${SBERT_FOUND}" ]]; then
    SHORT="${SBERT_FOUND/#$HOME/~}"
    report READY "SBert MiniLM" "cached at ${SHORT}"
else
    report WARN "SBert MiniLM" "not cached — first FAISS query will download ~80MB"
fi

# 16) FAISS index file.
FAISS_PATH="${PROJECT_ROOT}/corpus/polymarket_index.faiss"
if [[ -r "${FAISS_PATH}" ]]; then
    SZ=$(stat -f%z "${FAISS_PATH}" 2>/dev/null || stat -c%s "${FAISS_PATH}" 2>/dev/null || echo 0)
    SZ_MB=$(awk -v b="${SZ}" 'BEGIN{printf "%.1f", b/1048576}')
    report READY "FAISS index" "${FAISS_PATH/#${PROJECT_ROOT}\//} (${SZ_MB} MB)"
else
    report FAIL "FAISS index" "${FAISS_PATH} missing or unreadable"
fi

# ---------------------------------------------------------------------------
# DEMO TRIGGER
# ---------------------------------------------------------------------------
section "DEMO TRIGGER"

if (( BACKEND_OK == 0 )); then
    report WARN "mock trigger" "skipped (backend down)"
    report WARN "event status (10s)" "skipped (backend down)"
    report WARN "SSE" "skipped (backend down)"
else
    # 17) POST /trigger/event {"mode":"mock"}.  Use curl's own time_total
    # for sub-second precision.
    TRIG_TMP=$(mktemp -t dr_trig.XXXXXX)
    TRIG_TIME=$(curl -sS -o "${TRIG_TMP}" -m 5 -w '%{time_total}' \
        -X POST -H "Content-Type: application/json" \
        -d '{"mode":"mock"}' "${BACKEND_URL}/trigger/event" 2>/dev/null || echo "0")
    TRIG_RESP=$(cat "${TRIG_TMP}" 2>/dev/null || echo "")
    rm -f "${TRIG_TMP}"
    EVT_ID=$(echo "${TRIG_RESP}" | jq -r '.event_id // empty' 2>/dev/null)
    TRIG_MS=$(awk -v t="${TRIG_TIME}" 'BEGIN{printf "%d", t*1000}')
    if [[ -n "${EVT_ID}" ]]; then
        if (( TRIG_MS < 500 )); then
            report READY "mock trigger" "event ${EVT_ID} scheduled in ${TRIG_MS}ms"
        elif (( TRIG_MS < 2000 )); then
            report WARN "mock trigger" "event ${EVT_ID} scheduled in ${TRIG_MS}ms (>500ms target)"
        else
            report WARN "mock trigger" "event ${EVT_ID} scheduled in ${TRIG_MS}ms (slow)"
        fi

        # 18) Wait 10s, then check status.
        sleep 10
        STATUS_RESP=$(curl -sS -m 5 "${BACKEND_URL}/events/${EVT_ID}" 2>/dev/null || echo "")
        EVT_STATUS=$(echo "${STATUS_RESP}" | jq -r '.status // empty' 2>/dev/null)
        if [[ "${EVT_STATUS}" == "SUBMITTED" ]]; then
            report READY "event status (10s)" "event ${EVT_ID} SUBMITTED"
        elif [[ -n "${EVT_STATUS}" ]]; then
            report WARN "event status (10s)" "event ${EVT_ID} status=${EVT_STATUS} (not yet SUBMITTED)"
        else
            report FAIL "event status (10s)" "could not read /events/${EVT_ID}"
        fi

        # 19) SSE.  curl will time out reading the open stream — that's fine,
        # we only care about the HTTP status of the first response line.
        SSE_CODE=$(curl -sS -o /tmp/_dr_sse.$$ -m 2 -w '%{http_code}' "${BACKEND_URL}/sse/events?event_id=${EVT_ID}" 2>/dev/null)
        SSE_CODE="${SSE_CODE:-000}"
        SSE_HEAD=$(head -c 80 /tmp/_dr_sse.$$ 2>/dev/null | tr '\n' ' ')
        rm -f /tmp/_dr_sse.$$
        if [[ "${SSE_CODE}" == "200" ]]; then
            if [[ "${SSE_HEAD}" == *"event: hello"* ]]; then
                report READY "SSE" "/sse/events?event_id=${EVT_ID} 200 (hello event)"
            else
                report READY "SSE" "/sse/events?event_id=${EVT_ID} 200"
            fi
        elif [[ "${SSE_CODE}" == "429" ]]; then
            report FAIL "SSE" "/sse/events 429 (rate-limited)"
        else
            report WARN "SSE" "/sse/events HTTP ${SSE_CODE}"
        fi
    else
        report FAIL "mock trigger" "POST /trigger/event returned no event_id"
        report WARN "event status (10s)" "skipped (trigger failed)"
        report WARN "SSE" "skipped (trigger failed)"
    fi
fi

# ---------------------------------------------------------------------------
# FRONTEND
# ---------------------------------------------------------------------------
section "FRONTEND"

if [[ -z "${UI_URL}" ]]; then
    report WARN "/operators" "skipped (ui down or wrong app on port)"
    report WARN "/events" "skipped (ui down or wrong app on port)"
else
    # 20) /operators page.  Next.js renders cards client-side, so SSR HTML
    # is sparse — fall back to the backend API for actual operator count.
    OPS_CODE=$(http_status "${UI_URL}/operators" 10)
    if [[ "${OPS_CODE}" == "000" ]]; then
        OPS_CODE=$(http_status "${UI_URL}/operators" 15)
    fi
    if [[ "${OPS_CODE}" == "200" ]]; then
        OPS_COUNT="?"
        if (( BACKEND_OK == 1 )); then
            OPS_COUNT=$(curl -sS -m 3 "${BACKEND_URL}/api/operators" 2>/dev/null | jq 'length' 2>/dev/null || echo "?")
        fi
        if [[ "${OPS_COUNT}" =~ ^[0-9]+$ ]] && (( OPS_COUNT >= 3 )); then
            report READY "/operators" "page 200 (${OPS_COUNT} operators via /api/operators)"
        elif [[ "${OPS_COUNT}" =~ ^[0-9]+$ ]]; then
            report WARN "/operators" "page 200 but only ${OPS_COUNT} operators in API (< 3)"
        else
            report WARN "/operators" "page 200 (operator count unknown — API check skipped)"
        fi
    else
        report FAIL "/operators" "HTTP ${OPS_CODE}"
    fi

    # 21) /events page.  Next.js dev compile may take >5s on first hit;
    # use a generous timeout and re-probe once on 000.
    EVT_CODE=$(http_status "${UI_URL}/events" 10)
    if [[ "${EVT_CODE}" == "000" ]]; then
        EVT_CODE=$(http_status "${UI_URL}/events" 15)
    fi
    if [[ "${EVT_CODE}" == "200" ]]; then
        # Cross-reference DB row count.
        EVENT_ROWS=$(db_count events)
        report READY "/events" "page 200 (db has ${EVENT_ROWS} events)"
    else
        report FAIL "/events" "HTTP ${EVT_CODE}"
    fi
fi

# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
printf "\n%s\n" "${LINE}"
TOTAL=$((READY_COUNT + WARN_COUNT + FAIL_COUNT))
SUMMARY=$(printf "%sSUMMARY%s: %s%d READY%s · %s%d WARN%s · %s%d FAIL%s (of %d checks)" \
    "${C_BOLD}" "${C_RESET}" \
    "${C_GREEN}" "${READY_COUNT}" "${C_RESET}" \
    "${C_YELLOW}" "${WARN_COUNT}" "${C_RESET}" \
    "${C_RED}" "${FAIL_COUNT}" "${C_RESET}" \
    "${TOTAL}")
printf "%s\n" "${SUMMARY}"

if (( FAIL_COUNT > 0 )); then
    printf "%sDemo cannot proceed — fix FAIL items first.%s\n" "${C_RED}" "${C_RESET}"
    exit 2
elif (( WARN_COUNT > 0 )); then
    if (( SEEDER_CONFIG_COUNT == 0 )); then
        printf "%sDemo can proceed in mock mode. Live mode needs seeder PKs.%s\n" "${C_YELLOW}" "${C_RESET}"
    else
        printf "%sDemo can proceed with caveats — review WARN items.%s\n" "${C_YELLOW}" "${C_RESET}"
    fi
    exit 1
else
    printf "%sAll systems READY for demo.%s\n" "${C_GREEN}" "${C_RESET}"
    exit 0
fi
