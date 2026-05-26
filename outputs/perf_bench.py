"""PolyglotAlpha v2 — Performance benchmark (10 dimensions).

Measurement-only. Does not modify source. Writes results to
outputs/perf_benchmark.json and outputs/perf_benchmark.md.
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.request
import urllib.error
import threading
import http.client
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "outputs" / "perf_benchmark.json"
OUT_MD = ROOT / "outputs" / "perf_benchmark.md"
PROG_LOG = ROOT / "outputs" / "perf_progress.log"
BACKEND = "http://localhost:8000"

results: dict = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "dimensions": {}}


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(PROG_LOG, "a") as f:
        f.write(line + "\n")


def pct(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    idx = min(int(len(s) * p), len(s) - 1)
    return s[idx]


def stats(samples: list[float]) -> dict:
    if not samples:
        return {"n": 0}
    return {
        "n": len(samples),
        "min_ms": round(min(samples) * 1000, 2),
        "p50_ms": round(statistics.median(samples) * 1000, 2),
        "p95_ms": round(pct(samples, 0.95) * 1000, 2),
        "p99_ms": round(pct(samples, 0.99) * 1000, 2),
        "max_ms": round(max(samples) * 1000, 2),
        "mean_ms": round(statistics.mean(samples) * 1000, 2),
    }


def http_get_timed(path: str) -> float:
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(BACKEND + path, timeout=30) as resp:
            resp.read()
    except Exception as exc:
        log(f"  WARN GET {path} failed: {exc}")
        return -1.0
    return time.perf_counter() - start


# ---------------------------------------------------------------------------
# Dim 1: Backend API response time
# ---------------------------------------------------------------------------
def bench_api() -> None:
    log("=== Dim 1: Backend API response time ===")
    endpoints = {
        "GET /health": "/health",
        "GET /events": "/events",
        "GET /events/{id}": "/events/110",
        "GET /events/{id}/bids": "/events/110/bids",
        "GET /agents/{addr}": "/agents/0xqwen_agent",
        "GET /leaderboard": "/leaderboard",
        "GET /builder_fees": "/builder_fees",
    }
    api_results: dict = {}
    N = 50
    for label, path in endpoints.items():
        samples: list[float] = []
        for _ in range(N):
            t = http_get_timed(path)
            if t >= 0:
                samples.append(t)
        s = stats(samples)
        api_results[label] = s
        log(f"  {label}: p50={s.get('p50_ms')}ms p95={s.get('p95_ms')}ms p99={s.get('p99_ms')}ms n={s.get('n')}")
    results["dimensions"]["1_api_response"] = {
        "target": "p50<50ms, p95<200ms",
        "endpoints": api_results,
    }


# ---------------------------------------------------------------------------
# Dim 2: SSE event-stream latency
# ---------------------------------------------------------------------------
def bench_sse() -> None:
    log("=== Dim 2: SSE event-stream latency ===")
    events_seen: list[tuple[float, str]] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            conn = http.client.HTTPConnection("localhost", 8000, timeout=120)
            conn.request("GET", "/sse/events", headers={"Accept": "text/event-stream"})
            resp = conn.getresponse()
            while not stop.is_set():
                line = resp.fp.readline()
                if not line:
                    break
                ts = time.perf_counter()
                ln = line.decode("utf-8", errors="ignore").strip()
                if ln.startswith("data:") or ln.startswith("event:"):
                    events_seen.append((ts, ln[:200]))
        except Exception as exc:
            log(f"  SSE reader error: {exc}")

    t0 = time.perf_counter()
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    time.sleep(0.5)  # let connection settle

    # Trigger an event
    title = f"sse-bench {int(time.time()*1e6)}"
    post_start = time.perf_counter()
    req = urllib.request.Request(
        BACKEND + "/trigger/event",
        data=json.dumps({"title": title, "source": "sse-bench", "language": "en", "run_in_background": True}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as exc:
        log(f"  SSE trigger failed: {exc}")
        results["dimensions"]["2_sse"] = {"error": str(exc)}
        stop.set()
        return
    post_ts = time.perf_counter()

    # Wait up to 90s for stream events
    deadline = time.perf_counter() + 90
    while time.perf_counter() < deadline:
        if len(events_seen) >= 8:
            break
        time.sleep(0.3)
    stop.set()
    th.join(timeout=2)

    first_event_delta_ms = None
    if events_seen:
        first_event_delta_ms = round((events_seen[0][0] - post_ts) * 1000, 2)
    spacings_ms: list[float] = []
    for i in range(1, len(events_seen)):
        spacings_ms.append((events_seen[i][0] - events_seen[i - 1][0]) * 1000)
    results["dimensions"]["2_sse"] = {
        "events_captured": len(events_seen),
        "first_event_after_post_ms": first_event_delta_ms,
        "spacing_ms": stats([s / 1000 for s in spacings_ms]) if spacings_ms else {"n": 0},
        "event_types": [e[1][:80] for e in events_seen[:12]],
    }
    log(f"  SSE: captured={len(events_seen)} first_event_after_post_ms={first_event_delta_ms}")


# ---------------------------------------------------------------------------
# Dim 3: End-to-end lifecycle p50/p95/p99
# ---------------------------------------------------------------------------
def bench_lifecycle() -> None:
    log("=== Dim 3: End-to-end lifecycle (10 events, sequential) ===")
    durations: list[float] = []
    statuses: list[str] = []
    for i in range(10):
        title = f"perf-bench-lifecycle-{int(time.time()*1e6)}-{i}"
        payload = json.dumps({"title": title, "source": "perf", "language": "en"}).encode()
        req = urllib.request.Request(
            BACKEND + "/trigger/event",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            log(f"  iter {i} HTTP {e.code}: {body[:120]}")
            continue
        except Exception as exc:
            log(f"  iter {i} ERR: {exc}")
            continue
        elapsed = time.perf_counter() - start
        try:
            j = json.loads(body)
            statuses.append(j.get("status", "?"))
        except Exception:
            statuses.append("?")
        durations.append(elapsed)
        log(f"  iter {i}: {elapsed:.2f}s status={statuses[-1] if statuses else '?'}")
    results["dimensions"]["3_lifecycle"] = {
        "target": "p50<90s, p95<120s",
        "n": len(durations),
        "p50_s": round(statistics.median(durations), 2) if durations else None,
        "p95_s": round(pct(durations, 0.95), 2) if durations else None,
        "p99_s": round(pct(durations, 0.99), 2) if durations else None,
        "min_s": round(min(durations), 2) if durations else None,
        "max_s": round(max(durations), 2) if durations else None,
        "mean_s": round(statistics.mean(durations), 2) if durations else None,
        "statuses": statuses,
    }
    log(f"  Lifecycle p50={results['dimensions']['3_lifecycle']['p50_s']}s p95={results['dimensions']['3_lifecycle']['p95_s']}s p99={results['dimensions']['3_lifecycle']['p99_s']}s")


# ---------------------------------------------------------------------------
# Dim 6: SQLite query performance
# ---------------------------------------------------------------------------
def bench_sqlite() -> None:
    log("=== Dim 6: SQLite query performance ===")
    db = ROOT / "polyglot_alpha.db"
    con = sqlite3.connect(str(db))
    queries = {
        "count corpus_markets": "SELECT COUNT(*) FROM corpus_markets",
        "events last 50": "SELECT * FROM events ORDER BY id DESC LIMIT 50",
        "bids by event_id": "SELECT * FROM bids WHERE event_id = 110",
        "corpus resolved limit100": "SELECT * FROM corpus_markets WHERE state='resolved' LIMIT 100",
        "count outcome YES (full scan)": "SELECT COUNT(*) FROM corpus_markets WHERE outcome='YES'",
        "leaderboard agg": "SELECT agent_address, COUNT(*) FROM bids GROUP BY agent_address",
    }
    q_results: dict = {}
    for label, q in queries.items():
        times: list[float] = []
        for _ in range(10):
            start = time.perf_counter()
            try:
                con.execute(q).fetchall()
            except Exception as exc:
                log(f"  {label} ERR: {exc}")
                break
            times.append((time.perf_counter() - start) * 1000)
        if times:
            q_results[label] = {
                "median_ms": round(statistics.median(times), 2),
                "min_ms": round(min(times), 2),
                "max_ms": round(max(times), 2),
            }
            log(f"  {label}: median {q_results[label]['median_ms']}ms")
    con.close()
    results["dimensions"]["6_sqlite"] = q_results


# ---------------------------------------------------------------------------
# Dim 7: Arc chain RPC latency
# ---------------------------------------------------------------------------
def bench_arc_rpc() -> None:
    log("=== Dim 7: Arc chain RPC latency ===")
    url = os.environ.get("ARC_RPC_URL", "https://rpc.testnet.arc.network")
    payload = json.dumps({"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}).encode()
    samples: list[float] = []
    err_ct = 0
    for i in range(30):
        try:
            req = urllib.request.Request(url, data=payload, headers={"content-type": "application/json"})
            start = time.perf_counter()
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            samples.append(time.perf_counter() - start)
        except Exception as exc:
            err_ct += 1
            if err_ct == 1:
                log(f"  RPC error (first): {exc}")
            if err_ct >= 3:
                log(f"  RPC giving up after {err_ct} errors")
                break
    s = stats(samples) if samples else {"n": 0, "errors": err_ct}
    s["errors"] = err_ct
    s["url"] = url
    results["dimensions"]["7_arc_rpc"] = s
    log(f"  Arc RPC: n={len(samples)} err={err_ct} p50={s.get('p50_ms')}ms p95={s.get('p95_ms')}ms")


# ---------------------------------------------------------------------------
# Dim 9: FAISS lookup latency
# ---------------------------------------------------------------------------
def bench_faiss() -> None:
    log("=== Dim 9: FAISS lookup latency ===")
    sys.path.insert(0, str(ROOT))
    try:
        # Try different import shapes
        try:
            from polyglot_alpha.corpus.lookup import find_similar  # type: ignore
            fn = find_similar
            sig = "find_similar"
        except (ImportError, AttributeError):
            from polyglot_alpha.corpus import lookup as _lookup  # type: ignore
            fn = None
            sig = f"available: {[n for n in dir(_lookup) if not n.startswith('_')]}"
            results["dimensions"]["9_faiss"] = {"error": f"find_similar not in lookup; {sig}"}
            log(f"  FAISS: find_similar not found. {sig}")
            return
    except Exception as exc:
        results["dimensions"]["9_faiss"] = {"error": f"import failed: {exc}"}
        log(f"  FAISS import failed: {exc}")
        return

    queries = [
        "Will Bitcoin hit $200K?",
        "Trump election",
        "China RRR cut",
        "AI model release",
        "SpaceX launch",
    ]
    times: list[float] = []
    # warm-up
    try:
        fn(queries[0], k=5)
    except Exception as exc:
        results["dimensions"]["9_faiss"] = {"error": f"call failed: {exc}", "signature": sig}
        log(f"  FAISS call failed: {exc}")
        return
    for q in queries * 10:
        start = time.perf_counter()
        try:
            fn(q, k=5)
        except Exception as exc:
            log(f"  FAISS query '{q[:30]}' err: {exc}")
            continue
        times.append((time.perf_counter() - start) * 1000)
    if times:
        results["dimensions"]["9_faiss"] = {
            "target": "<100ms median",
            "n": len(times),
            "median_ms": round(statistics.median(times), 2),
            "p95_ms": round(pct([t / 1000 for t in times], 0.95) * 1000, 2),
            "min_ms": round(min(times), 2),
            "max_ms": round(max(times), 2),
        }
        log(f"  FAISS median={results['dimensions']['9_faiss']['median_ms']}ms p95={results['dimensions']['9_faiss']['p95_ms']}ms")
    else:
        results["dimensions"]["9_faiss"] = {"n": 0, "error": "all calls failed"}


def main() -> None:
    PROG_LOG.write_text("")
    log("PolyglotAlpha v2 perf benchmark START")

    # Dim 1
    bench_api()
    # Dim 6 (cheap, independent)
    bench_sqlite()
    # Dim 7 (network)
    bench_arc_rpc()
    # Dim 9 (FAISS)
    bench_faiss()
    # Dim 2 (SSE, needs trigger)
    bench_sse()
    # Dim 3 (lifecycle) — last, since it takes a while and might be slow
    bench_lifecycle()

    results["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    OUT_JSON.write_text(json.dumps(results, indent=2))
    log(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
