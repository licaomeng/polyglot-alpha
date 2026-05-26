#!/usr/bin/env bash
# W10 test suite runner — invoke pytest, jest, and tsc one after the other
# and dump tail-trimmed logs to /tmp so the W10 sub-agent can grep them.
#
# Idempotent + safe to re-run. Does not modify source. Exits non-zero if
# any of the three suites surfaced failures.
#
# Usage (from repo root):
#   bash scripts/w10/test_suite_runner.sh
#   bash scripts/w10/test_suite_runner.sh --no-jest   # skip jest if you know UI tests are flaky
#   bash scripts/w10/test_suite_runner.sh --no-tsc
#
# Logs:
#   /tmp/w10-pytest.log
#   /tmp/w10-jest.log
#   /tmp/w10-tsc.log
#   /tmp/w10-test-suite.summary
set -uo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

VENV_PY="$REPO_ROOT/.venv/bin/python"
PYTEST_LOG="/tmp/w10-pytest.log"
JEST_LOG="/tmp/w10-jest.log"
TSC_LOG="/tmp/w10-tsc.log"
SUMMARY="/tmp/w10-test-suite.summary"

RUN_PYTEST=1
RUN_JEST=1
RUN_TSC=1

for arg in "$@"; do
  case "$arg" in
    --no-pytest) RUN_PYTEST=0 ;;
    --no-jest)   RUN_JEST=0 ;;
    --no-tsc)    RUN_TSC=0 ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $arg (use --help)" >&2
      exit 2
      ;;
  esac
done

# Sanity check the virtualenv early — if missing, we still try the system
# python so this is best-effort but visible.
if [ ! -x "$VENV_PY" ]; then
  echo "WARN: $VENV_PY not found; falling back to 'python3'" >&2
  VENV_PY="$(command -v python3 || true)"
  if [ -z "$VENV_PY" ]; then
    echo "FATAL: no python interpreter available" >&2
    exit 2
  fi
fi

PYTEST_RC=0
JEST_RC=0
TSC_RC=0

# ── pytest ────────────────────────────────────────────────────────────────
if [ "$RUN_PYTEST" = "1" ]; then
  echo "[w10-test-suite] running pytest…"
  : > "$PYTEST_LOG"
  "$VENV_PY" -m pytest tests/ -x -q 2>&1 | tail -80 > "$PYTEST_LOG"
  PYTEST_RC=${PIPESTATUS[0]}
else
  echo "[w10-test-suite] skipping pytest"
  echo "skipped" > "$PYTEST_LOG"
fi

# ── jest (UI) ─────────────────────────────────────────────────────────────
if [ "$RUN_JEST" = "1" ]; then
  echo "[w10-test-suite] running jest…"
  : > "$JEST_LOG"
  (cd ui && npx jest --silent 2>&1) | tail -60 > "$JEST_LOG"
  JEST_RC=${PIPESTATUS[0]}
else
  echo "[w10-test-suite] skipping jest"
  echo "skipped" > "$JEST_LOG"
fi

# ── tsc --noEmit ─────────────────────────────────────────────────────────
if [ "$RUN_TSC" = "1" ]; then
  echo "[w10-test-suite] running tsc --noEmit…"
  : > "$TSC_LOG"
  (cd ui && npx tsc --noEmit 2>&1) | tail -40 > "$TSC_LOG"
  TSC_RC=${PIPESTATUS[0]}
else
  echo "[w10-test-suite] skipping tsc"
  echo "skipped" > "$TSC_LOG"
fi

# ── Aggregate summary ────────────────────────────────────────────────────
echo "=== W10 test suite summary ===" | tee "$SUMMARY"
echo "repo: $REPO_ROOT"             | tee -a "$SUMMARY"
echo "ts: $(date -u +%FT%TZ)"       | tee -a "$SUMMARY"
echo                                  | tee -a "$SUMMARY"

echo "--- pytest (rc=$PYTEST_RC) ---" | tee -a "$SUMMARY"
grep -E "passed|failed|error" "$PYTEST_LOG" | tail -5 | tee -a "$SUMMARY"
echo                                  | tee -a "$SUMMARY"

echo "--- jest (rc=$JEST_RC) ---"     | tee -a "$SUMMARY"
grep -E "Tests:|Test Suites:" "$JEST_LOG" | tee -a "$SUMMARY"
echo                                  | tee -a "$SUMMARY"

echo "--- tsc (rc=$TSC_RC) ---"       | tee -a "$SUMMARY"
TSC_ERRORS=$(grep -cE "error TS" "$TSC_LOG" || true)
echo "tsc errors: $TSC_ERRORS"        | tee -a "$SUMMARY"
echo                                  | tee -a "$SUMMARY"
echo "log paths:"                     | tee -a "$SUMMARY"
echo "  $PYTEST_LOG"                  | tee -a "$SUMMARY"
echo "  $JEST_LOG"                    | tee -a "$SUMMARY"
echo "  $TSC_LOG"                     | tee -a "$SUMMARY"

# Non-zero rc if anything failed.
OVERALL_RC=0
[ "$PYTEST_RC" -ne 0 ] && OVERALL_RC=1
[ "$JEST_RC"   -ne 0 ] && OVERALL_RC=1
[ "$TSC_RC"    -ne 0 ] && OVERALL_RC=1
[ "$TSC_ERRORS" -ne 0 ] && OVERALL_RC=1

if [ "$OVERALL_RC" -eq 0 ]; then
  echo "OVERALL: PASS" | tee -a "$SUMMARY"
else
  echo "OVERALL: FAIL" | tee -a "$SUMMARY"
fi

exit "$OVERALL_RC"
