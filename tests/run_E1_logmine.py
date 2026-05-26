"""E1 log-archeology helper.

Re-runs 6 PASS events at INFO level to a dedicated log file so we can
mine WARNING/ERROR/Traceback patterns that the orchestrator emits during
a normal happy-path run. Does NOT touch outputs/E1_stress_audit/audit_event_*.json.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Configure file-only logger BEFORE importing polyglot modules.
LOG_PATH = ROOT / "outputs" / "E1_stress_audit" / "E1_orchestrator_info.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from tests._pass_path_mocks import install_mocks, uninstall_mocks  # noqa: E402
from tests.run_pass_path_audit import _winner_address  # noqa: E402


async def _main() -> None:
    install_mocks()
    from polyglot_alpha.orchestrator import BidRecord, run_lifecycle

    op = _winner_address()
    variations = [
        # 3 qualified, varied
        [
            BidRecord(op, 0.50, 5.0, None, None, 0.85),
            BidRecord("0x" + "b" * 40, 0.30, 5.0, None, None, 0.92),
            BidRecord("0x" + "c" * 40, 0.75, 5.0, None, None, 0.75),
        ],
        # Mixed qualified/unqualified
        [
            BidRecord(op, 0.50, 5.0, None, None, 0.95),
            BidRecord("0x" + "d" * 40, 0.20, 5.0, None, None, 0.6),
            BidRecord("0x" + "e" * 40, 0.55, 5.0, None, None, 0.8),
        ],
        # solo
        [BidRecord(op, 0.40, 5.0, None, None, 0.85)],
        # tie
        [
            BidRecord(op, 0.50, 5.0, None, None, 0.85),
            BidRecord("0x" + "f" * 40, 0.50, 5.0, None, None, 0.90),
        ],
        # 5 bids
        [
            BidRecord(op, 0.60, 5.0, None, None, 0.80),
            BidRecord("0x" + "a" * 40, 0.40, 5.0, None, None, 0.95),
            BidRecord("0x" + "b" * 40, 0.35, 5.0, None, None, 0.85),
            BidRecord("0x" + "c" * 40, 0.55, 5.0, None, None, 0.78),
            BidRecord("0x" + "d" * 40, 0.45, 5.0, None, None, 0.90),
        ],
        # repeat one for sample size
        [
            BidRecord(op, 0.50, 5.0, None, None, 0.85),
            BidRecord("0x" + "b" * 40, 0.30, 5.0, None, None, 0.92),
            BidRecord("0x" + "c" * 40, 0.75, 5.0, None, None, 0.75),
        ],
    ]
    try:
        for i, bids in enumerate(variations, 1):
            salt = uuid.uuid4().hex[:8]
            event_dict = {
                "title": f"E1-logmine-{i}-{salt} Will FOMC raise rates June 2026?",
                "sources": [
                    {"name": "logmine", "url": f"https://logmine.example/{salt}", "language": "en"}
                ],
                "language": "en",
                "category": "macro",
                "summary": "E1 logmine synthetic source.",
            }
            t0 = time.monotonic()
            res = await run_lifecycle(
                event_dict,
                auction_window_seconds=0.0,
                mock_bids=bids,
                auction_mode="mock",
                confirm_real_polymarket=False,
            )
            dt = time.monotonic() - t0
            print(f"[logmine {i}] event={res.get('event_id')} status={res.get('status')} {dt:.2f}s")
    finally:
        uninstall_mocks()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
