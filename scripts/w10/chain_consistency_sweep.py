#!/usr/bin/env python3
"""W10 sweep: run ``verify_chain_consistency.py`` over the N most recent
SUBMITTED events and aggregate the results by phase.

This script is a **thin orchestrator** around
``scripts/verify_chain_consistency.py``. It does NOT reimplement any of the
chain<->DB matching logic — it invokes the existing verifier as a subprocess
and parses its stdout, then prints a tally + a per-phase diff section for
every FAIL.

Design goals (matches the W10-PREP brief):

* Idempotent + safe to re-run anytime (verifier is read-only; this script
  only reads).
* Default sweep window: latest 20 SUBMITTED events. Configurable via
  ``--limit N``. Filter by mode via ``--mode {live,mock,any}``.
* Output: a summary table (``Phase X: PASS Y/N, FAIL Z/N, SKIP W/N``) and,
  for each FAIL, the verifier's full diff output + a heuristic
  "suggested root cause" line.
* Exit code = number of FAIL phases across the sweep (0 on clean run).

Usage::

    .venv/bin/python scripts/w10/chain_consistency_sweep.py
    .venv/bin/python scripts/w10/chain_consistency_sweep.py --limit 30 --mode live
    .venv/bin/python scripts/w10/chain_consistency_sweep.py --out /tmp/w10-chain-sweep.md

The Markdown report file (default ``/tmp/w10-chain-sweep.md``) is
overwritten on each run.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_DB_PATH: Path = _REPO_ROOT / "polyglot_alpha.db"
_VERIFIER: Path = _REPO_ROOT / "scripts" / "verify_chain_consistency.py"
_VENV_PY: Path = _REPO_ROOT / ".venv" / "bin" / "python"

# Phases reported by the verifier — kept in lockstep with PhaseResult.name.
_PHASE_NAMES: tuple[str, ...] = (
    "Phase 2 Auction",
    "Phase 4 Judges",
    "Phase 5 Anchor",
    "Phase 7 Fee Split",
    "Phase 8 Reputation",
)

_DEFAULT_REPORT: Path = Path("/tmp/w10-chain-sweep.md")


@dataclass
class PhaseTally:
    """Aggregate counts for one phase across the sweep."""

    name: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    fail_events: list[int] = field(default_factory=list)

    @property
    def checked(self) -> int:
        return self.passed + self.failed

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped


@dataclass
class EventVerdict:
    """One event's full verifier output."""

    event_id: int
    mode: str
    overall: str  # "PASS" | "FAIL" | "ERROR"
    phase_status: dict[str, str] = field(default_factory=dict)
    raw_output: str = ""
    return_code: int = 0


def _recent_submitted_event_ids(
    limit: int,
    *,
    mode_filter: str,
) -> list[tuple[int, str]]:
    """Return ``[(event_id, mode), ...]`` newest first."""

    if not _DB_PATH.exists():
        raise SystemExit(f"FATAL: DB not found at {_DB_PATH}")
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if mode_filter == "any":
            sql = (
                "SELECT id, mode FROM events WHERE status = 'SUBMITTED' "
                "ORDER BY id DESC LIMIT ?"
            )
            params: tuple = (limit,)
        else:
            sql = (
                "SELECT id, mode FROM events WHERE status = 'SUBMITTED' "
                "AND mode = ? ORDER BY id DESC LIMIT ?"
            )
            params = (mode_filter, limit)
        rows = conn.execute(sql, params).fetchall()
        return [(int(r["id"]), str(r["mode"])) for r in rows]
    finally:
        conn.close()


_PHASE_HEADER_RE = re.compile(r"^(Phase \d+ [A-Za-z ]+)$")
_RESULT_LINE_RE = re.compile(r"^\s*RESULT:\s*(PASS|FAIL)\b")
_SKIP_LINE_RE = re.compile(r"^\s*SKIP\s*[—-]")


def _parse_phase_statuses(stdout: str) -> dict[str, str]:
    """Walk the verifier stdout and map phase name -> status.

    The verifier prints one section per phase. Each section starts on a
    line that is exactly the phase name (e.g. ``Phase 2 Auction``) and is
    terminated either by another phase header or by the OVERALL line.
    """

    statuses: dict[str, str] = {}
    current: Optional[str] = None
    for raw in stdout.splitlines():
        line = raw.rstrip()
        match = _PHASE_HEADER_RE.match(line.strip())
        if match and match.group(1) in _PHASE_NAMES:
            current = match.group(1)
            # Default to UNKNOWN; will be overwritten when the RESULT/SKIP
            # line appears below.
            statuses.setdefault(current, "UNKNOWN")
            continue
        if current is None:
            continue
        if _SKIP_LINE_RE.match(line):
            statuses[current] = "SKIP"
            current = None
            continue
        m_res = _RESULT_LINE_RE.match(line)
        if m_res:
            statuses[current] = m_res.group(1)
            current = None
    return statuses


def _suggest_root_cause(
    phase_name: str,
    section_text: str,
) -> str:
    """Best-effort one-liner explaining the most likely cause of a FAIL.

    We do not try to be exhaustive; we just match the most common verifier
    failure modes seen during W7 / W9 so an operator can triage quickly.
    """

    text = section_text.lower()
    if "settle tx receipt status=revert" in text or "settle tx receipt status=unknown" in text:
        return (
            "settlement tx never confirmed or reverted on Arc — check RPC "
            "and re-submit settle in orchestrator if needed."
        )
    if "winner mismatch" in text:
        return (
            "DB winner_address differs from on-chain getAuction().winner — "
            "stale placeholder fixture or post-settle DB rewrite."
        )
    if "winning_bid mismatch" in text:
        return (
            "DB winning_bid drifted from on-chain value — unit-conversion "
            "bug (USDC base units vs decimal) or partial fill."
        )
    if "title_hash mismatch" in text:
        return (
            "anchor digest mismatch — orchestrator likely committed a "
            "different candidate hash than the one persisted; rerun "
            "Phase 5 with current candidate."
        )
    if "no questions row" in text or "missing tx_hash" in text:
        return (
            "Phase 5 row missing — anchor commit never reached DB. Check "
            "QuestionRegistry RPC + question_registry_client error logs."
        )
    if "attestation tx status" in text:
        return (
            "JudgePanel attestation tx reverted — likely insufficient "
            "judge weight or stale dossier; rerun verify with W9-A patch."
        )
    if "cumulative fees on chain are less than the sum of db legs" in text:
        return (
            "fee split inconsistency — DB recorded legs but BuilderFeeRouter "
            "balance hasn't increased; tx may have reverted silently."
        )
    if "no agent_reputation row" in text:
        return (
            "Reputation row missing for winner — Phase 8 hook never fired; "
            "check ReputationRegistry write authority + W9-B handler."
        )
    if "drift" in text and "cumulative_fees" in text:
        return (
            "soft drift between DB cumulative_fees and chain cum_fees — "
            "likely an unconfirmed/pending tx; safe to re-check after "
            "next block."
        )
    if "rpc call failed" in text:
        return "RPC unreachable or contract address wrong — verify .env."
    return "see diff above; no auto-classification matched."


def _extract_phase_section(stdout: str, phase_name: str) -> str:
    """Return the slice of verifier stdout describing ``phase_name``."""

    lines = stdout.splitlines()
    start: Optional[int] = None
    for idx, raw in enumerate(lines):
        if raw.strip() == phase_name:
            start = idx
            break
    if start is None:
        return ""
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        match = _PHASE_HEADER_RE.match(stripped)
        if match and match.group(1) in _PHASE_NAMES and match.group(1) != phase_name:
            end = idx
            break
        if stripped.startswith("OVERALL:"):
            end = idx
            break
    return "\n".join(lines[start:end]).rstrip()


def _run_verifier(event_id: int) -> EventVerdict:
    """Invoke ``verify_chain_consistency.py`` for ``event_id``."""

    if not _VENV_PY.exists():
        python_exe = sys.executable
    else:
        python_exe = str(_VENV_PY)

    env = os.environ.copy()
    try:
        proc = subprocess.run(
            [python_exe, str(_VERIFIER), str(event_id), "--verbose"],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        combined = stdout if not stderr else f"{stdout}\n---stderr---\n{stderr}"
        statuses = _parse_phase_statuses(stdout)
        # Promote ERROR verdict if the verifier could not produce phase
        # output at all (e.g. RPC build failure, missing DB row).
        if not statuses and proc.returncode != 0:
            overall = "ERROR"
        elif proc.returncode == 0:
            overall = "PASS"
        else:
            overall = "FAIL"
        return EventVerdict(
            event_id=event_id,
            mode="",  # filled in by caller
            overall=overall,
            phase_status=statuses,
            raw_output=combined,
            return_code=proc.returncode,
        )
    except subprocess.TimeoutExpired:
        return EventVerdict(
            event_id=event_id,
            mode="",
            overall="ERROR",
            raw_output="verifier timed out after 120s",
            return_code=-1,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return EventVerdict(
            event_id=event_id,
            mode="",
            overall="ERROR",
            raw_output=f"verifier invocation failed: {exc}",
            return_code=-1,
        )


def _render_summary_table(
    tallies: dict[str, PhaseTally],
    total_events: int,
) -> str:
    """Render the per-phase summary as a Markdown table."""

    header = (
        "| Phase | PASS | FAIL | SKIP | Total |\n"
        "|-------|------|------|------|-------|\n"
    )
    rows = []
    for name in _PHASE_NAMES:
        t = tallies[name]
        rows.append(
            f"| {name} | {t.passed} | {t.failed} | {t.skipped} | "
            f"{t.total} |"
        )
    rows.append(
        f"| **events sampled** | — | — | — | **{total_events}** |"
    )
    return header + "\n".join(rows)


def _render_report(
    verdicts: list[EventVerdict],
    tallies: dict[str, PhaseTally],
    *,
    limit: int,
    mode_filter: str,
) -> str:
    lines: list[str] = []
    lines.append("# W10 chain<->DB consistency sweep")
    lines.append("")
    lines.append(
        f"- events scanned: **{len(verdicts)}** (limit={limit}, mode={mode_filter})"
    )
    overall_counts = Counter(v.overall for v in verdicts)
    lines.append(
        f"- per-event overall: "
        f"PASS={overall_counts.get('PASS', 0)} "
        f"FAIL={overall_counts.get('FAIL', 0)} "
        f"ERROR={overall_counts.get('ERROR', 0)}"
    )
    lines.append("")
    lines.append("## Per-phase tally")
    lines.append("")
    lines.append(_render_summary_table(tallies, len(verdicts)))
    lines.append("")

    fail_records: list[tuple[int, str, str, str]] = []
    for v in verdicts:
        for phase in _PHASE_NAMES:
            if v.phase_status.get(phase) == "FAIL":
                section = _extract_phase_section(v.raw_output, phase)
                cause = _suggest_root_cause(phase, section)
                fail_records.append((v.event_id, v.mode, phase, section + "\n\nROOT-CAUSE GUESS: " + cause))
        if v.overall == "ERROR":
            fail_records.append(
                (
                    v.event_id,
                    v.mode,
                    "verifier-error",
                    v.raw_output
                    + "\n\nROOT-CAUSE GUESS: verifier could not complete; "
                    "check RPC env vars + DB row existence.",
                )
            )

    if not fail_records:
        lines.append("## FAIL diffs")
        lines.append("")
        lines.append("_no FAIL phases — sweep is clean_")
    else:
        lines.append(f"## FAIL diffs ({len(fail_records)})")
        lines.append("")
        for event_id, mode, phase, section in fail_records:
            lines.append(f"### event {event_id} ({mode}) — {phase}")
            lines.append("")
            lines.append("```")
            lines.append(section.strip())
            lines.append("```")
            lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep verify_chain_consistency.py over the N most recent "
            "SUBMITTED events and aggregate results."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of recent SUBMITTED events to sweep (default 20)",
    )
    parser.add_argument(
        "--mode",
        choices=("live", "mock", "any"),
        default="any",
        help="Filter events by mode (default: any)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_REPORT,
        help=f"Markdown report path (default: {_DEFAULT_REPORT})",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-event stdout; only print summary at the end.",
    )
    args = parser.parse_args()

    if not _VERIFIER.exists():
        print(f"FATAL: verifier not found at {_VERIFIER}", file=sys.stderr)
        return 2

    events = _recent_submitted_event_ids(args.limit, mode_filter=args.mode)
    if not events:
        print(
            f"WARN: no SUBMITTED events match mode={args.mode}; nothing to do.",
            file=sys.stderr,
        )
        # Still write an empty report so downstream agents see something.
        args.out.write_text("# W10 chain<->DB consistency sweep\n\n_no events_\n")
        return 0

    print(
        f"[sweep] scanning {len(events)} events "
        f"(mode={args.mode}, limit={args.limit})"
    )

    verdicts: list[EventVerdict] = []
    tallies: dict[str, PhaseTally] = {
        name: PhaseTally(name=name) for name in _PHASE_NAMES
    }

    for event_id, mode in events:
        if not args.quiet:
            print(f"[sweep] verifying event_id={event_id} mode={mode}")
        v = _run_verifier(event_id)
        v.mode = mode
        verdicts.append(v)
        for phase in _PHASE_NAMES:
            status = v.phase_status.get(phase, "SKIP")
            tally = tallies[phase]
            if status == "PASS":
                tally.passed += 1
            elif status == "FAIL":
                tally.failed += 1
                tally.fail_events.append(event_id)
            else:
                tally.skipped += 1

    report = _render_report(
        verdicts, tallies, limit=args.limit, mode_filter=args.mode
    )
    args.out.write_text(report)
    print(f"\n[sweep] wrote report to {args.out}")

    # Console summary.
    print("\n=== per-phase summary ===")
    for name in _PHASE_NAMES:
        t = tallies[name]
        print(
            f"  {name}: PASS {t.passed}/{t.total}, "
            f"FAIL {t.failed}/{t.total}, SKIP {t.skipped}/{t.total}"
        )
    overall_counts = Counter(v.overall for v in verdicts)
    print(
        "\nper-event overall: "
        f"PASS={overall_counts.get('PASS', 0)} "
        f"FAIL={overall_counts.get('FAIL', 0)} "
        f"ERROR={overall_counts.get('ERROR', 0)}"
    )

    total_fails = sum(t.failed for t in tallies.values())
    return 1 if total_fails > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
