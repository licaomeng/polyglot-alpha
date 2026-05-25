"""Run the COMET judge against outputs/sample_0.json end-to-end.

Run with the project venv:
    .venv/bin/python scripts/score_sample.py
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from polyglot_alpha.judges import PanelQuestion
from polyglot_alpha.judges.translation import judge_comet

SAMPLE = Path(__file__).resolve().parent.parent / "outputs" / "sample_0.json"


async def main() -> None:
    payload = json.loads(SAMPLE.read_text())
    question = PanelQuestion(
        title=payload["title"],
        description=payload.get("description", ""),
        resolution_criteria=payload.get("resolution_criteria", ""),
        resolution_source=payload.get("resolution_source"),
        source_news=payload.get("source_news"),
        category=payload.get("category"),
        cutoff_ts=payload.get("cutoff_ts"),
    )
    t0 = time.perf_counter()
    result = await judge_comet(question)
    elapsed = time.perf_counter() - t0
    print(f"COMET judge on sample_0.json — elapsed {elapsed:.2f}s")
    print(json.dumps(result.__dict__, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
