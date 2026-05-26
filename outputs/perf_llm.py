"""Dim 8: LLM provider latency (4 providers via make_llm)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load .env
env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from polyglot_alpha.llm import make_llm, GEMINI_FLASH, DEEPSEEK_V3, QWEN_25, LLAMA_33  # noqa: E402

PROMPT = "Translate to French in one short sentence: 'The market is open.'"

async def time_call(label: str, model_id: str) -> dict:
    fn = make_llm(model_id)
    start = time.perf_counter()
    try:
        out = await fn(PROMPT)
        elapsed = time.perf_counter() - start
        return {
            "provider": label,
            "model": model_id,
            "elapsed_s": round(elapsed, 3),
            "output_len": len(out),
            "ok": True,
        }
    except Exception as exc:
        return {
            "provider": label,
            "model": model_id,
            "error": str(exc)[:200],
            "ok": False,
        }


async def main() -> None:
    providers = [
        ("Gemini-2.0-Flash", GEMINI_FLASH),
        ("DeepSeek-V3", DEEPSEEK_V3),
        ("Qwen-2.5-72B", QWEN_25),
        ("Llama-3.3-70B", LLAMA_33),
    ]
    results = []
    for label, model_id in providers:
        r = await time_call(label, model_id)
        print(f"  {label}: {r}", flush=True)
        results.append(r)
    out = ROOT / "outputs" / "perf_llm.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"Wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
