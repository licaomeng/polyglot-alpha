"""Track backend RSS+CPU during a wall-clock window. Writes JSONL samples."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 55511
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 900
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/Users/messili/codebase/polyglot-alpha/outputs/perf_resource.jsonl")

OUT.write_text("")
start = time.time()
end = start + DURATION
while time.time() < end:
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(PID), "-o", "rss=,pcpu="], text=True, timeout=2
        ).strip()
        if not out:
            break
        rss_kb, cpu = out.split()
        rec = {"t": time.time() - start, "rss_kb": int(rss_kb), "cpu": float(cpu)}
        with OUT.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        break
    time.sleep(2)
