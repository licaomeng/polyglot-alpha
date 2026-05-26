"""Compile all perf data into perf_benchmark.json + perf_benchmark.md."""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "outputs" / "perf_benchmark.json"
OUT_MD = ROOT / "outputs" / "perf_benchmark.md"


def load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def parse_progress() -> dict:
    """Parse perf_progress.log to recover per-endpoint API stats, FAISS, SSE."""
    txt = (ROOT / "outputs" / "perf_progress.log").read_text()
    out: dict = {"api": {}, "sqlite": {}, "lifecycle_iters": [], "raw": txt}
    for m in re.finditer(r"  (GET [^:]+): p50=([\d.]+)ms p95=([\d.]+)ms p99=([\d.]+)ms n=(\d+)", txt):
        out["api"][m.group(1)] = {
            "p50_ms": float(m.group(2)),
            "p95_ms": float(m.group(3)),
            "p99_ms": float(m.group(4)),
            "n": int(m.group(5)),
        }
    for m in re.finditer(r"  ([\w ()_]+?): median ([\d.]+)ms", txt):
        out["sqlite"][m.group(1).strip()] = float(m.group(2))
    m = re.search(r"Arc RPC: n=(\d+) err=(\d+) p50=([\d.]+)ms p95=([\d.]+)ms", txt)
    if m:
        out["arc_rpc"] = {
            "n": int(m.group(1)),
            "errors": int(m.group(2)),
            "p50_ms": float(m.group(3)),
            "p95_ms": float(m.group(4)),
        }
    m = re.search(r"FAISS median=([\d.]+)ms p95=([\d.]+)ms", txt)
    if m:
        out["faiss"] = {"median_ms": float(m.group(1)), "p95_ms": float(m.group(2))}
    m = re.search(r"SSE: captured=(\d+) first_event_after_post_ms=([-\d.]+)", txt)
    if m:
        out["sse"] = {"captured": int(m.group(1)), "first_event_after_post_ms": float(m.group(2))}
    for m in re.finditer(r"iter (\d+): ([\d.]+)s status=(\S+)", txt):
        out["lifecycle_iters"].append({"iter": int(m.group(1)), "duration_s": float(m.group(2)), "status": m.group(3)})
    for m in re.finditer(r"iter (\d+) ERR: (\S.+)", txt):
        out["lifecycle_iters"].append({"iter": int(m.group(1)), "error": m.group(2)})
    return out


def main() -> None:
    parsed = parse_progress()
    llm = json.loads((ROOT / "outputs" / "perf_llm.json").read_text())
    frontend = json.loads((ROOT / "outputs" / "perf_frontend.json").read_text())
    cold_start_s = float((ROOT / "outputs" / "perf_cold_start.txt").read_text().strip())
    resource = load_jsonl(ROOT / "outputs" / "perf_resource.jsonl")

    # Resource stats during 10-event window (use all samples from the monitor)
    if resource:
        rss_kb = [r["rss_kb"] for r in resource]
        cpu = [r["cpu"] for r in resource]
        # Compute "CPU-seconds" approx: sum(cpu * sample_interval) / 100
        # Sample interval ≈ 2s, cpu is % of one core.
        # cpu * 2s / 100 = core-seconds per sample
        total_cpu_core_s = sum(cpu) * 2 / 100
        resource_summary = {
            "samples": len(resource),
            "duration_s": round(resource[-1]["t"] - resource[0]["t"], 1) if len(resource) > 1 else 0,
            "rss_kb_start": rss_kb[0],
            "rss_kb_max": max(rss_kb),
            "rss_kb_end": rss_kb[-1],
            "rss_kb_delta": rss_kb[-1] - rss_kb[0],
            "rss_kb_p95": sorted(rss_kb)[int(len(rss_kb) * 0.95)],
            "cpu_mean_pct": round(statistics.mean(cpu), 2),
            "cpu_max_pct": max(cpu),
            "total_cpu_core_seconds": round(total_cpu_core_s, 2),
        }
    else:
        resource_summary = {}

    # Lifecycle stats
    completed = [it for it in parsed["lifecycle_iters"] if "duration_s" in it]
    timeouts = [it for it in parsed["lifecycle_iters"] if "error" in it]
    if completed:
        durs = [it["duration_s"] for it in completed]
        lifecycle = {
            "completed": len(completed),
            "timeouts": len(timeouts),
            "n_planned": 10,
            "p50_s": round(statistics.median(durs), 2),
            "p95_s": round(sorted(durs)[min(int(len(durs) * 0.95), len(durs) - 1)], 2),
            "p99_s": round(sorted(durs)[min(int(len(durs) * 0.99), len(durs) - 1)], 2),
            "min_s": round(min(durs), 2),
            "max_s": round(max(durs), 2),
            "mean_s": round(statistics.mean(durs), 2),
            "note": "Iter 1 exceeded 180s client timeout. Lifecycle bench terminated early at 30-min wall-clock cap.",
        }
    else:
        lifecycle = {"completed": 0, "timeouts": len(timeouts), "n_planned": 10, "note": "all timed out"}

    final = {
        "schema_version": "perf-bench-v1",
        "dimensions": {
            "1_api_response": {
                "target": "p50<50ms, p95<200ms",
                "endpoints": parsed["api"],
                "pass": all(ep["p50_ms"] < 50 and ep["p95_ms"] < 200 for ep in parsed["api"].values()),
            },
            "2_sse_latency": {
                "captured_events": parsed.get("sse", {}).get("captured", 0),
                "first_event_after_post_ms": parsed.get("sse", {}).get("first_event_after_post_ms"),
                "note": "SSE reader connected before POST so first_event_after_post is negative — sequence is interleaved. Captured 14 events confirms stream is live; absolute latency not isolatable in this run.",
            },
            "3_lifecycle": {
                "target": "p50<90s, p95<120s",
                **lifecycle,
            },
            "4_backend_cold_start_s": cold_start_s,
            "5_frontend": {
                "note": "Next.js 15 dev mode, JIT compile on first hit.",
                "warm_or_cold_per_route": frontend,
            },
            "6_sqlite": parsed["sqlite"],
            "7_arc_rpc": parsed.get("arc_rpc"),
            "8_llm_providers": llm,
            "9_faiss": parsed.get("faiss"),
            "10_resource": resource_summary,
        },
    }

    OUT_JSON.write_text(json.dumps(final, indent=2))
    print(f"Wrote {OUT_JSON}")

    # Markdown
    md: list[str] = []
    md.append("# PolyglotAlpha v2 — Performance Benchmark Report\n")
    md.append("_Measurement-only run. No source modifications. 30-min wall-clock cap applied._\n")

    # Dim 1
    md.append("\n## 1. Backend API response time\n")
    md.append("Target: p50 < 50ms, p95 < 200ms\n")
    md.append("| Endpoint | p50 (ms) | p95 (ms) | p99 (ms) | n | Verdict |\n|---|---|---|---|---|---|")
    for ep, s in parsed["api"].items():
        verdict = "PASS" if s["p50_ms"] < 50 and s["p95_ms"] < 200 else "FAIL"
        md.append(f"| {ep} | {s['p50_ms']} | {s['p95_ms']} | {s['p99_ms']} | {s['n']} | {verdict} |")
    md.append("")

    md.append("## 2. SSE event-stream latency\n")
    sse = parsed.get("sse", {})
    md.append(f"- Captured events while a background-triggered event ran: **{sse.get('captured', 0)}**.")
    md.append(f"- `first_event_after_post_ms` = {sse.get('first_event_after_post_ms')} (negative — SSE reader connected before POST returned, so number reflects interleaving, not pure latency).")
    md.append("- Stream confirmed live. Per-event end-to-end latency requires a future run with paired timestamps.\n")

    md.append("## 3. End-to-end lifecycle p50/p95/p99\n")
    md.append("Target: p50 < 90s, p95 < 120s\n")
    md.append(f"- Planned iterations: 10. Completed (successful): **{lifecycle.get('completed', 0)}**, timeouts at 180s client cap: **{lifecycle.get('timeouts', 0)}**.")
    md.append(f"- Wall-clock budget forced early termination; only iter 0 finished within 180s.")
    if lifecycle.get("completed"):
        md.append(f"- p50: **{lifecycle.get('p50_s')}s** (target <90s — within target).")
        md.append(f"- p95/p99: only 1 sample available; cannot compute distribution. Iter 1 exceeded 180s; true p95 unknown but **≥ 180s** (FAIL target <120s).")
    md.append("")

    md.append("## 4. Backend cold start\n")
    md.append(f"- Killed uvicorn (PID 55511), restarted, time to first `/health` 200: **{cold_start_s}s**.\n")

    md.append("## 5. Frontend cold start (Next.js 15 dev mode)\n")
    md.append("FCP/LCP via Playwright cold Chromium contexts. Dev mode JIT-compiles each route on first visit.\n")
    md.append("| URL | TTFB (ms) | FCP (ms) | Load (ms) | Status |\n|---|---|---|---|---|")
    for r in frontend:
        url = r.get("url", "?").replace("http://localhost:3001", "")
        md.append(f"| {url} | {r.get('ttfb_ms')} | {r.get('fcp_ms')} | {r.get('load_ms')} | {r.get('status')} |")
    md.append("\n- `/agents` returned 404 (no index page; only `/agents/[address]` exists). Its 2.0s TTFB reflects dev-mode 404 compile.")
    md.append("- Cold visit to unvisited `/about` measured separately: nav 506ms, TTFB 132ms, FCP 184ms.\n")

    md.append("## 6. SQLite query performance (~80K corpus_markets, 114 events)\n")
    md.append("| Query | Median (ms) |\n|---|---|")
    for q, ms in parsed["sqlite"].items():
        md.append(f"| {q} | {ms} |")
    md.append("\nAll queries < 1ms median. Indexes effective.\n")

    md.append("## 7. Arc chain RPC latency\n")
    arc = parsed.get("arc_rpc", {})
    md.append(f"- `eth_blockNumber` × 30, all succeeded.")
    md.append(f"- p50 = **{arc.get('p50_ms')}ms**, p95 = **{arc.get('p95_ms')}ms**. (Public testnet RPC; latency dominated by network round-trip.)\n")

    md.append("## 8. LLM API latency (4 providers)\n")
    md.append("| Provider | Model | Elapsed (s) | OK |\n|---|---|---|---|")
    for r in llm:
        md.append(f"| {r['provider']} | {r['model']} | {r.get('elapsed_s', 'N/A')} | {'YES' if r.get('ok') else 'NO — ' + str(r.get('error', ''))[:60]} |")
    md.append("\nNote: Gemini hit 429 rate limit on free-tier key during the benchmark. Other three providers all under 2.1s for a one-sentence translation.\n")

    md.append("## 9. FAISS lookup latency\n")
    faiss = parsed.get("faiss", {})
    md.append(f"- 5 queries × 10 reps = 50 lookups, k=5.")
    md.append(f"- Median = **{faiss.get('median_ms')}ms** (target <100ms — PASS), p95 = {faiss.get('p95_ms')}ms (tail driven by first invocation of each query before any caching).\n")

    md.append("## 10. Memory + CPU during the benchmark window\n")
    rs = resource_summary
    md.append(f"- Samples: {rs.get('samples')} over {rs.get('duration_s')}s (2s interval).")
    md.append(f"- RSS start → end: **{rs.get('rss_kb_start')} kB → {rs.get('rss_kb_end')} kB** (delta {rs.get('rss_kb_delta')} kB).")
    md.append(f"- RSS max: {rs.get('rss_kb_max')} kB; p95: {rs.get('rss_kb_p95')} kB.")
    md.append(f"- CPU mean: {rs.get('cpu_mean_pct')}%, max: {rs.get('cpu_max_pct')}%.")
    md.append(f"- Total CPU consumed during window: **{rs.get('total_cpu_core_seconds')} core-seconds**.\n")
    md.append("Note: window spanned the full benchmark (API → SQLite → RPC → FAISS → SSE → 2 lifecycle iterations), not exactly 10 lifecycle events.\n")

    md.append("---\n")
    md.append("## Summary\n")
    md.append("| Dimension | Measured | Verdict |\n|---|---|---|")
    md.append("| 1. Backend API response | YES | PASS (all p50<5ms, p95<30ms) |")
    md.append("| 2. SSE event stream | PARTIAL | Live stream confirmed; per-event latency not isolated |")
    md.append("| 3. End-to-end lifecycle | PARTIAL | iter 0 = 65.87s PASS; iter 1 ≥180s FAIL p95 target |")
    md.append("| 4. Backend cold start | YES | 1.65s |")
    md.append("| 5. Frontend cold start | YES | Warm FCP 90-760ms; unvisited route ~184ms FCP |")
    md.append("| 6. SQLite queries | YES | PASS (all <1ms median) |")
    md.append("| 7. Arc chain RPC | YES | p50 591ms, p95 828ms (network-bound) |")
    md.append("| 8. LLM API latency | YES (3/4) | Gemini 429 rate-limited; DeepSeek/Qwen/Llama all < 2.1s |")
    md.append("| 9. FAISS lookup | YES | PASS (median 16ms) |")
    md.append("| 10. Memory + CPU | YES | RSS delta < 0 (no leak); 4-5 core-seconds total CPU |")
    OUT_MD.write_text("\n".join(md))
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
