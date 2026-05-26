"""Audit reachability of every RSS source listed in ``sources.json``.

For each source, perform a real HTTP GET and report:
  - HTTP status code
  - Round-trip latency (ms)
  - Number of feed entries parsed by feedparser
  - Bytes fetched
  - Final verdict: ``OK``, ``BAD_STATUS``, ``TIMEOUT``, ``CONN_ERR``, ``PARSE_ERR``

This script is the W13-C diagnostic counterpart to ``rss_aggregator.py``: it
exists so an operator can reproduce, on demand, the per-source health view
without spinning up the full API surface. Run it before claiming "live mode
works" — if fewer than ``MIN_HEALTHY_SOURCES`` succeed, the live RSS path
will fail-loud at runtime.

Usage::

    python scripts/audit_rss_sources.py                # default 15s timeout
    python scripts/audit_rss_sources.py --timeout 30
    python scripts/audit_rss_sources.py --json         # machine-readable

Exit code:
    0  if at least ``--min-healthy`` sources succeed (default 2)
    1  otherwise — surface to CI / operator
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import feedparser
import httpx

# Make ``polyglot_alpha`` importable when the script is run from the repo root
# without an installed package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from polyglot_alpha.ingestion.rss_aggregator import (  # noqa: E402
    DEFAULT_USER_AGENT,
    load_sources,
)

DEFAULT_TIMEOUT_S: float = 15.0
DEFAULT_MIN_HEALTHY: int = 2


@dataclass(frozen=True)
class AuditResult:
    name: str
    url: str
    language: str
    status_code: int | None
    latency_ms: int
    bytes_fetched: int
    entries_parsed: int
    verdict: str
    error: str | None

    def healthy(self) -> bool:
        return self.verdict == "OK" and self.entries_parsed > 0


async def _audit_one(
    client: httpx.AsyncClient, src: dict[str, object]
) -> AuditResult:
    name = str(src.get("name", "<unnamed>"))
    url = str(src.get("url", ""))
    language = str(src.get("language", "unknown"))
    if not src.get("enabled", True):
        return AuditResult(
            name=name,
            url=url,
            language=language,
            status_code=None,
            latency_ms=0,
            bytes_fetched=0,
            entries_parsed=0,
            verdict="DISABLED",
            error=str(src.get("disabled_reason") or "marked enabled=false"),
        )
    started = time.perf_counter()
    try:
        resp = await client.get(url)
    except httpx.TimeoutException as exc:
        return AuditResult(
            name=name,
            url=url,
            language=language,
            status_code=None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            bytes_fetched=0,
            entries_parsed=0,
            verdict="TIMEOUT",
            error=str(exc) or "request timed out",
        )
    except httpx.HTTPError as exc:
        return AuditResult(
            name=name,
            url=url,
            language=language,
            status_code=None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            bytes_fetched=0,
            entries_parsed=0,
            verdict="CONN_ERR",
            error=str(exc),
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    body = resp.content or b""
    if resp.status_code >= 400:
        return AuditResult(
            name=name,
            url=url,
            language=language,
            status_code=resp.status_code,
            latency_ms=latency_ms,
            bytes_fetched=len(body),
            entries_parsed=0,
            verdict="BAD_STATUS",
            error=f"HTTP {resp.status_code}",
        )
    # 2xx-3xx: try to parse to confirm it's really a feed.
    try:
        parsed = feedparser.parse(body)
        entries = len(getattr(parsed, "entries", []) or [])
    except Exception as exc:  # pragma: no cover - feedparser is robust
        return AuditResult(
            name=name,
            url=url,
            language=language,
            status_code=resp.status_code,
            latency_ms=latency_ms,
            bytes_fetched=len(body),
            entries_parsed=0,
            verdict="PARSE_ERR",
            error=str(exc),
        )
    return AuditResult(
        name=name,
        url=url,
        language=language,
        status_code=resp.status_code,
        latency_ms=latency_ms,
        bytes_fetched=len(body),
        entries_parsed=entries,
        verdict="OK" if entries > 0 else "PARSE_ERR",
        error=None if entries > 0 else "feed parsed to zero entries",
    )


async def audit_all(timeout: float) -> list[AuditResult]:
    sources = load_sources()
    async with httpx.AsyncClient(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        return await asyncio.gather(*(_audit_one(client, s) for s in sources))


def _print_table(results: list[AuditResult]) -> None:
    headers = ("Source", "URL", "Lang", "Status", "Latency", "Entries", "Verdict")
    rows: list[tuple[str, ...]] = []
    for r in results:
        status_str = str(r.status_code) if r.status_code is not None else "-"
        url_display = r.url if len(r.url) <= 60 else r.url[:57] + "..."
        rows.append(
            (
                r.name,
                url_display,
                r.language,
                status_str,
                f"{r.latency_ms}ms",
                str(r.entries_parsed),
                r.verdict,
            )
        )
    widths = [
        max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)
    ]
    fmt = " | ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="Per-request HTTP timeout in seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--min-healthy",
        type=int,
        default=DEFAULT_MIN_HEALTHY,
        help=(
            "Exit non-zero unless at least this many sources are healthy "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a human-readable table.",
    )
    args = parser.parse_args()

    results = asyncio.run(audit_all(timeout=args.timeout))
    healthy = sum(1 for r in results if r.healthy())
    disabled = sum(1 for r in results if r.verdict == "DISABLED")
    total = len(results)
    broken = total - healthy - disabled

    if args.json:
        payload = {
            "total_sources": total,
            "healthy": healthy,
            "broken": broken,
            "disabled": disabled,
            "results": [asdict(r) for r in results],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_table(results)
        print()
        print(
            f"Summary: {healthy}/{total} healthy · "
            f"{broken} broken · {disabled} disabled · "
            f"min required = {args.min_healthy}"
        )
        if broken:
            print("Broken sources:")
            for r in results:
                if not r.healthy() and r.verdict != "DISABLED":
                    print(f"  - {r.name}: {r.verdict} ({r.error or '?'})")

    return 0 if healthy >= args.min_healthy else 1


if __name__ == "__main__":
    sys.exit(main())
