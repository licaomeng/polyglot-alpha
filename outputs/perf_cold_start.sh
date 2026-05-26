#!/bin/bash
# Dim 4: Backend cold-start. Kills uvicorn, restarts, times until /health returns 200.
set -e
cd /Users/messili/codebase/polyglot-alpha

# Find current PID
OLD_PID=$(pgrep -f "uvicorn.*polyglot_alpha" | head -1)
echo "Old PID: $OLD_PID"

if [ -n "$OLD_PID" ]; then
  kill "$OLD_PID" 2>/dev/null || true
  # Wait for process to actually die
  for _ in $(seq 1 20); do
    if ! kill -0 "$OLD_PID" 2>/dev/null; then break; fi
    sleep 0.2
  done
fi

# Make sure port is free
sleep 1

# Cold restart
START=$(python3 -c "import time; print(time.time())")
nohup .venv/bin/python -m uvicorn polyglot_alpha.api.main:app --host 127.0.0.1 --port 8000 --log-level warning > /tmp/perf_uvicorn.log 2>&1 &
NEW_PID=$!
echo "New PID: $NEW_PID"

# Poll /health
ATTEMPTS=0
while ! curl -fs http://localhost:8000/health >/dev/null 2>&1; do
  sleep 0.1
  ATTEMPTS=$((ATTEMPTS + 1))
  if [ $ATTEMPTS -gt 200 ]; then
    echo "FAIL: backend did not respond within 20s"
    exit 1
  fi
done
END=$(python3 -c "import time; print(time.time())")

ELAPSED=$(python3 -c "print(round($END - $START, 3))")
echo "BACKEND_COLD_START_S=$ELAPSED"
echo "NEW_PID=$NEW_PID"
echo "$ELAPSED" > outputs/perf_cold_start.txt
echo "$NEW_PID" > outputs/perf_backend.pid
