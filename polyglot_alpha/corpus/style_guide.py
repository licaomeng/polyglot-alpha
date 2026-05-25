"""LLM-distil a Polymarket question writing style guide.

We hand the LLM a random sample of ~100 high-volume questions (drawn
across as many categories as the corpus contains) and ask for a 5-10
bullet style guide. The model output is written to
``corpus/style_guide.md`` verbatim — we do not post-process beyond
stripping the leading/trailing whitespace and unwrapping any ``markdown``
code fences the model insists on producing.

The async LLM helper lives in ``polyglot_alpha.llm``; we drive it via
``asyncio.run`` because the rest of the corpus tooling is sync.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import random
from pathlib import Path
from typing import Awaitable, Callable, Optional

import pandas as pd

from polyglot_alpha import llm as llm_module

LOGGER = logging.getLogger(__name__)

DEFAULT_SAMPLE_SIZE = 100
DEFAULT_OUTPUT = Path("corpus/style_guide.md")
DEFAULT_SEED = 1337

SYSTEM_PROMPT = (
    "You are a senior prediction-market editor at Polymarket. "
    "You write tight, resolvable, journalistically neutral question titles."
)

USER_PROMPT_TEMPLATE = """\
Below are {n} real Polymarket question titles, sampled across categories:

{sample}

Distill the Polymarket question style into a concise Markdown document.
Cover these dimensions explicitly:

1. **Structure** — typical sentence shape, length, punctuation, capitalization.
2. **Tone** — register, point of view, journalistic vs. casual voice.
3. **Resolution clarity** — how unambiguous the YES/NO outcome must be.
4. **Granularity** — date precision, numeric thresholds, named entities.
5. **Leading-question avoidance** — pitfalls like loaded framing, double-barrelled questions, or speculation about intent.

Output requirements:
- Use a top-level `# Polymarket Question Style Guide` heading.
- Provide 5 to 10 bullet points total (across sub-sections is fine).
- Each bullet should be a single, action-oriented sentence.
- Do NOT wrap the output in a code fence.
"""

LLMComplete = Callable[..., Awaitable[str]]


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def sample_questions(
    df: pd.DataFrame,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
) -> list[str]:
    """Return a stratified random sample of question titles."""

    if df.empty:
        return []

    sample_size = min(sample_size, len(df))
    if "category" not in df.columns:
        rng = random.Random(seed)
        questions = df["question"].dropna().astype(str).tolist()
        rng.shuffle(questions)
        return questions[:sample_size]

    rng = random.Random(seed)
    by_cat: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        q = str(row.get("question") or "").strip()
        if not q:
            continue
        cat = str(row.get("category") or "").strip().lower() or "_other"
        by_cat.setdefault(cat, []).append(q)

    if not by_cat:
        return []

    # Round-robin sample to spread categories.
    for v in by_cat.values():
        rng.shuffle(v)
    picks: list[str] = []
    cats = list(by_cat.keys())
    rng.shuffle(cats)
    while len(picks) < sample_size:
        progressed = False
        for cat in cats:
            if not by_cat[cat]:
                continue
            picks.append(by_cat[cat].pop())
            progressed = True
            if len(picks) >= sample_size:
                break
        if not progressed:
            break
    return picks


def _format_user_prompt(samples: list[str]) -> str:
    numbered = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(samples))
    return USER_PROMPT_TEMPLATE.format(n=len(samples), sample=numbered)


async def distill_style_guide(
    df: pd.DataFrame,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
    llm_complete: Optional[LLMComplete] = None,
) -> str:
    """Sample, prompt, and post-process. Returns the Markdown body."""

    samples = sample_questions(df, sample_size=sample_size, seed=seed)
    if not samples:
        raise ValueError("Corpus is empty; cannot distill style guide")
    prompt = _format_user_prompt(samples)
    runner = llm_complete or llm_module.complete
    LOGGER.info("Calling LLM for style distillation (n=%d)", len(samples))
    text = await runner(prompt, system=SYSTEM_PROMPT)
    return _strip_fences(text)


def save_style_guide(markdown: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not markdown.endswith("\n"):
        markdown += "\n"
    dest.write_text(markdown)
    return dest


# --------------------------------------------------------------------------- #
# CLI.                                                                        #
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parquet", default="corpus/polymarket_questions.parquet"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING")
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    df = pd.read_parquet(args.parquet)
    markdown = asyncio.run(
        distill_style_guide(
            df, sample_size=args.sample_size, seed=args.seed
        )
    )
    out = save_style_guide(markdown, Path(args.out))
    LOGGER.info("wrote style guide -> %s", out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
