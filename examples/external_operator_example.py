"""External operator example — bid with a single-shot LLM, no internal debate.

This file demonstrates the minimum surface a 3rd-party operator needs to
plug into Polyglot Alpha. The operator here:

* Skips our internal debate loop entirely (no critics, no moderator, no
  refine pass).
* Calls an LLM exactly once per event.
* Computes the candidate_hash and prints what they would submit on-chain.

It exists to make it obvious that the platform is a thin protocol with
reference seeders — not a monolithic system that forces every operator
to use the same debate machinery. The four reference seeders we ship
(gemini, deepseek, qwen, llama) happen to use the internal debate loop;
external operators are free to use anything: single-shot, multi-round,
RAG, fine-tuned models, rule-based templates, humans in the loop.

Usage:

    # Run with a real LLM (needs OPENROUTER_API_KEY for openai/gpt-4o-mini):
    export OPENROUTER_API_KEY=...
    python examples/external_operator_example.py

    # Run fully offline (no network, deterministic output):
    python examples/external_operator_example.py --mock
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

# Make the example runnable from a fresh checkout without an editable install.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from polyglot_alpha.agent_sdk import (  # noqa: E402  — sys.path hack above
    BidIntent,
    CandidateQuestion,
    EventPayload,
)
from polyglot_alpha.llm import MockLLM, make_llm  # noqa: E402

logger = logging.getLogger("external_operator_example")


SAMPLE_EVENT: EventPayload = {
    "event_id": "0xexternal_sample_event",
    "title_zh": "中国宣布对部分美国商品加征关税",
    "body_zh": (
        "中国财政部宣布将对部分美国进口商品加征关税，反制美方近期发布的"
        "新一轮关税清单。新关税将在两周内生效。"
    ),
    "url": "https://example.com/cn/news/external-001",
    "cutoff_ts": int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp()),
    "topic": "geopolitics",
    "source": "example",
}


# ---------------------------------------------------------------------------
# A minimal external operator
# ---------------------------------------------------------------------------


_SINGLE_SHOT_PROMPT = (
    "You are an external operator on Polyglot Alpha. Convert the following "
    "Chinese news event into a binary YES/NO Polymarket question.\n\n"
    "Return STRICT JSON with keys: question_en, resolution_criteria, "
    "end_date_iso, tags (list of 2-4 strings).\n\n"
    "TITLE: {title}\n"
    "BODY: {body}\n"
)


def _default_end_date_iso() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(days=30)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_candidate(raw_json: Dict[str, Any]) -> CandidateQuestion:
    """Project an arbitrary LLM response into the SDK CandidateQuestion shape.

    Anything missing falls back to a safe default so we never broadcast a
    half-shaped candidate. The on-chain hash stays valid, but the quality
    judges downstream would punish the operator's reputation.
    """

    return {
        "question_en": str(raw_json.get("question_en") or "").strip()
        or "Will the announced action take effect by the cutoff?",
        "resolution_criteria": str(
            raw_json.get("resolution_criteria") or ""
        ).strip()
        or "Resolves YES if the action takes effect by the end_date_iso.",
        "end_date_iso": str(raw_json.get("end_date_iso") or "").strip()
        or _default_end_date_iso(),
        "tags": [str(t) for t in (raw_json.get("tags") or [])][:4],
        "meta": {"operator": "external-example", "method": "single-shot"},
    }


async def generate_candidate(
    event: EventPayload, *, mock: bool = False
) -> CandidateQuestion:
    """Single-shot LLM call. No debate, no critics, no refine."""

    llm = (
        MockLLM(model_id="external-example-mock")
        if mock
        else make_llm("openai/gpt-4o-mini")
    )
    prompt = _SINGLE_SHOT_PROMPT.format(
        title=event.get("title_zh") or "",
        body=event.get("body_zh") or "",
    )
    raw = await llm(prompt)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON; using safe defaults")
        parsed = {}
    return _coerce_candidate(parsed if isinstance(parsed, dict) else {})


def hash_candidate(candidate: CandidateQuestion) -> str:
    """Deterministic SHA-256 over the candidate dict.

    Sorted keys + compact separators give us a canonical byte string,
    so two operators that produced the same dict will land on the same
    on-chain hash regardless of dict insertion order.
    """

    encoded = json.dumps(dict(candidate), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def build_bid_intent(
    event: EventPayload, candidate: CandidateQuestion, *, bid_amount_usdc: float
) -> BidIntent:
    return {
        "event_id": event["event_id"],
        "bid_amount_usdc": bid_amount_usdc,
        "candidate_hash_hex": hash_candidate(candidate),
        "candidate": candidate,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _amain(mock: bool) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("== External operator example ==")
    print(f"event_id    : {SAMPLE_EVENT['event_id']}")
    print(f"title_zh    : {SAMPLE_EVENT['title_zh']}")
    print(f"mode        : {'MOCK (offline)' if mock else 'LIVE LLM'}")

    candidate = await generate_candidate(SAMPLE_EVENT, mock=mock)
    bid = build_bid_intent(SAMPLE_EVENT, candidate, bid_amount_usdc=0.42)

    print("\n-- candidate --")
    print(json.dumps(candidate, indent=2, ensure_ascii=False))
    print("\n-- bid intent (would be submitted to TranslationAuction) --")
    print(json.dumps(bid, indent=2, ensure_ascii=False))

    # Sanity check: hash is reproducible.
    assert hash_candidate(candidate) == bid["candidate_hash_hex"]
    print("\nOK -- candidate_hash matches the candidate that would be committed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Skip the live LLM call and use MockLLM (deterministic, offline).",
    )
    args = parser.parse_args()
    return asyncio.run(_amain(mock=args.mock))


if __name__ == "__main__":
    raise SystemExit(main())
