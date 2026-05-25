"""Main entry point for the backtest framework.

``run_backtest`` ties together:

1. Loading N resolved markets (parquet or mock fallback).
2. Reverse-engineering a synthetic Chinese-language news event for each
   so the existing pipeline can run unchanged.
3. Running a mock 4-agent auction (the live on-chain auction is replaced
   with a deterministic in-process winner pick — the existing bid
   strategies still decide the winner).
4. Running the winning agent's pipeline to produce a candidate Question.
5. Running the 11-judge panel against the candidate.
6. Scoring vs. the historical outcome and computing hypothetical ROI.

The framework is async because the agent pipeline + judge panel are
both async; ``run_backtest`` is a synchronous wrapper that drives the
event loop. Use ``run_backtest_async`` directly from async contexts
(notebooks, FastAPI handlers).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional

from ..llm import LLMCallable, MockLLM
from ..schemas import (
    AnalystReport,
    NewsEvent,
    Question,
    TranslationCandidate,
)
from .outcome_matcher import (
    OutcomeComparison,
    compare_questions,
    infer_category,
)
from .roi_estimator import RoiEstimate, estimate_roi

LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Defaults & types                                                            #
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESOLVED_PARQUET = _REPO_ROOT / "corpus" / "polymarket_resolved.parquet"
DEFAULT_SAMPLE_GLOB = _REPO_ROOT / "outputs"  # sample_*.json placeholders
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "outputs" / "backtest"

AGENT_NAMES: tuple[str, ...] = ("gemini", "deepseek", "qwen", "llama")
# Hex-stub wallets so the per-market record looks realistic without
# touching any real chain state.
_AGENT_WALLET_STUBS: dict[str, str] = {
    "gemini": "0xG3M1N1" + "0" * 34,
    "deepseek": "0xD33P53" + "0" * 34,
    "qwen": "0xQW3N25" + "0" * 34,
    "llama": "0xLL4M43" + "0" * 34,
}

LLMFactory = Callable[[str], LLMCallable]


@dataclass
class MarketRecord:
    """Minimal view of a resolved Polymarket question."""

    market_id: str
    question: str
    category: str
    outcome: str
    total_volume_usdc: float
    uma_dispute: bool
    resolution_source: str

    @classmethod
    def from_row(cls, row: dict) -> "MarketRecord":
        category = str(row.get("category") or "") or infer_category(str(row.get("question") or ""))
        return cls(
            market_id=str(row.get("market_id") or ""),
            question=str(row.get("question") or ""),
            category=category,
            outcome=str(row.get("outcome") or ""),
            total_volume_usdc=float(row.get("total_volume_usdc") or 0.0),
            uma_dispute=bool(row.get("uma_dispute") or False),
            resolution_source=str(row.get("resolution_source") or ""),
        )


@dataclass
class BacktestResult:
    """Per-market backtest record (one row of ``per_market_results.jsonl``)."""

    market_id: str
    actual_question: str
    actual_outcome: str
    actual_volume: float
    agent_winner: str
    agent_winner_address: str
    agent_question: str
    judge_verdict: str
    judge_score: float
    semantic_similarity: float
    outcome_match: bool
    estimated_roi_usdc: float
    uma_dispute: bool
    category: str
    notes: str
    # Internal / extra fields for the report builder.
    framing_predicted: str = ""
    capture_rate: float = 0.0
    builder_fee_usdc: float = 0.0
    d5_passed: Optional[bool] = None
    bids: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Mock LLM for deterministic, fast runs.                                      #
# --------------------------------------------------------------------------- #


class BacktestMockLLM:
    """Deterministic LLM that produces a usable analyst summary AND a
    pipeline-compatible JSON candidate.

    The base ``MockLLM`` always returns the same canned JSON, which
    works for ``translators.propose_candidates`` but not for
    ``analysts.run_analysts`` (which parses ``SUMMARY: ... JSON: ...``
    format). This mock branches on whether the prompt looks like an
    analyst prompt or a translator prompt.
    """

    def __init__(self, agent_name: str, market: "MarketRecord") -> None:
        self.agent_name = agent_name
        self.market = market

    async def __call__(self, prompt: str) -> str:
        await asyncio.sleep(0)
        if "analyst" in prompt.lower() and "polymarket house style" not in prompt.lower():
            return self._analyst_response()
        return self._translator_response()

    def _analyst_response(self) -> str:
        return (
            f"SUMMARY: Mock analyst summary for market {self.market.market_id} "
            f"({self.agent_name}): the event concerns {self.market.question[:80]}.\n"
            'JSON: {"entities": ["entity_a"], "risks": ["risk_a"]}'
        )

    def _translator_response(self) -> str:
        # Mirror the actual question so semantic similarity is non-trivial
        # but inject the agent name so different agents produce different
        # candidates (the synthesizer picks longest resolution_criteria).
        q = self.market.question.strip()
        suffix = "" if q.endswith("?") else "?"
        question_en = f"{q}{suffix}"
        criteria_lengths = {"gemini": 80, "deepseek": 120, "qwen": 60, "llama": 100}
        n_pad = criteria_lengths.get(self.agent_name, 80)
        resolution_criteria = (
            "Resolves YES if the underlying event occurs by the cutoff "
            f"(synthetic backtest criteria from {self.agent_name})."
        ).ljust(n_pad, ".")
        payload = {
            "question_en": question_en,
            "resolution_criteria": resolution_criteria,
            "end_date_iso": "2026-12-31T23:59:59Z",
            "tags": [self.market.category, "backtest"],
        }
        return json.dumps(payload)


def make_backtest_llm_factory(market: MarketRecord, *, real: bool = False) -> LLMFactory:
    """Return a per-agent LLM factory.

    ``real=True`` uses the existing :func:`polyglot_alpha.llm.make_llm`
    (which itself falls back to ``MockLLM`` if API keys are absent).
    ``real=False`` uses :class:`BacktestMockLLM`.
    """

    if real:
        from ..llm import make_llm

        def _factory(model_id: str) -> LLMCallable:
            return make_llm(model_id)

        return _factory

    def _mock_factory(model_id: str) -> LLMCallable:  # noqa: ARG001 — unused
        # ``model_id`` is unused for the mock — the agent identity is
        # captured via closure when ``_run_agent_pipeline`` builds the LLM.
        return MockLLM(model_id=model_id)

    return _mock_factory


# --------------------------------------------------------------------------- #
# Reverse-engineer a "trigger event" so the agent pipeline has input.         #
# --------------------------------------------------------------------------- #


def synthesize_trigger_event(market: MarketRecord) -> NewsEvent:
    """Construct a NewsEvent that could plausibly have triggered ``market``.

    For the deterministic backtest path we don't need a real Chinese
    headline — the pipeline only requires the ``title_zh`` / ``body_zh``
    fields to be populated. We re-use the market's own question as the
    Chinese body (the analysts + translators run downstream regardless).
    """

    body = (
        f"事件背景: {market.question}\n"
        f"类别: {market.category}\n"
        f"参考来源: {market.resolution_source or 'unknown'}"
    )
    return NewsEvent(
        event_id=f"backtest-{market.market_id}",
        url=market.resolution_source or "https://backtest.local/",
        title_zh=market.question,
        body_zh=body,
        cutoff_ts=int(time.time()) + 86400,
        topic=market.category,
        source="backtest",
    )


# --------------------------------------------------------------------------- #
# Mock auction: pick a winner from the four bid strategies.                   #
# --------------------------------------------------------------------------- #


def _build_bid_table(event_dict: dict[str, Any]) -> dict[str, float]:
    """Run each agent's static ``bid_strategy`` against the event dict.

    We instantiate the agent classes with a fake wallet PK so we don't
    need any real chain state. The bid strategies are pure functions
    over the event dict, so the construction is cheap.
    """

    from ..agents import AGENT_REGISTRY

    bids: dict[str, float] = {}
    for name, cls in AGENT_REGISTRY.items():
        # ``base.py`` validates the PK is truthy; the value itself is
        # never used because we don't touch the chain.
        agent = cls(wallet_pk="0x" + "11" * 32)
        bids[name] = float(agent.bid_strategy(event_dict))
    return bids


def _pick_winner(bids: dict[str, float], *, rng: random.Random) -> str:
    """Auction logic: lowest bid wins.

    The on-chain auction is reputation-weighted (score = bid / rep) but
    we treat reputation as 1.0 across the board for the backtest. Ties
    are broken by deterministic RNG so reruns with the same seed
    produce the same winner.
    """

    if not bids:
        raise ValueError("no bids provided")
    min_bid = min(bids.values())
    candidates = sorted(name for name, b in bids.items() if abs(b - min_bid) < 1e-9)
    return rng.choice(candidates)


# --------------------------------------------------------------------------- #
# Agent pipeline (decoupled from on-chain plumbing).                          #
# --------------------------------------------------------------------------- #


async def _run_agent_pipeline(
    agent_name: str,
    market: MarketRecord,
    *,
    llm_factory: LLMFactory,
    mock_llm: bool,
) -> Question:
    """Run analysts -> translators -> synthesizer for one agent.

    Mirrors ``BaseTranslatorAgent.run_pipeline`` but takes a
    ``MarketRecord`` instead of a chain-event dict, and avoids
    constructing the agent class (which insists on a wallet PK).
    """

    from .. import analysts, quality_eval, synthesizer, translators

    event = synthesize_trigger_event(market)
    if mock_llm:
        llm: LLMCallable = BacktestMockLLM(agent_name=agent_name, market=market)
    else:
        llm = llm_factory(_model_for(agent_name))

    reports: list[AnalystReport] = await analysts.run_analysts(event, llm)
    candidates: list[TranslationCandidate] = await translators.propose_candidates(
        event, reports, llm
    )
    question = synthesizer.synthesize(event, candidates)
    score = quality_eval.score_question(question)
    question.confidence = score.score
    question.quality_score = score.score
    return question


def _model_for(agent_name: str) -> str:
    """Map agent name -> LLM model id (same routing the live agents use)."""

    from ..llm import DEEPSEEK_V3, GEMINI_FLASH, LLAMA_33, QWEN_25

    table = {
        "gemini": GEMINI_FLASH,
        "deepseek": DEEPSEEK_V3,
        "qwen": QWEN_25,
        "llama": LLAMA_33,
    }
    return table.get(agent_name, GEMINI_FLASH)


# --------------------------------------------------------------------------- #
# Judge panel — wrapped so a missing FAISS index doesn't crash the run.       #
# --------------------------------------------------------------------------- #


async def _run_judges(
    question: Question,
    market: MarketRecord,
    *,
    llm_factory: LLMFactory,
    mock_llm: bool,
) -> dict[str, Any]:
    """Run the 11-judge panel and return a JSON-serializable verdict dict.

    Failures (missing optional model weights, network errors, etc.) are
    caught and reported as a synthetic FAIL verdict so the backtest run
    never aborts mid-stream.
    """

    from ..judges.panel import evaluate

    panel_payload = {
        "title": question.question_en,
        "description": question.resolution_criteria,
        "resolution_criteria": question.resolution_criteria,
        "resolution_source": market.resolution_source or "",
        "cutoff_ts": question.end_date_iso,
        "category": market.category,
        "source_news": market.question,
        "source_language": "en",
        "target_language": "en",
        "reference_translation": market.question,
    }

    llm_call: Optional[LLMCallable] = None
    if not mock_llm:
        try:
            llm_call = llm_factory("gemini-2.0-flash")
        except Exception:
            llm_call = None

    try:
        verdict = await evaluate(panel_payload, market.question, llm_call=llm_call)
        return verdict.as_dict()
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("judge panel crashed for market=%s", market.market_id)
        return {
            "overall_pass": False,
            "verdict": "FAIL",
            "overall_score": 0,
            "translation_scores": {},
            "style_alignment_passes": {},
            "judge_results": [],
            "notes": [f"panel crashed: {exc!r}"],
        }


# --------------------------------------------------------------------------- #
# Market loader.                                                              #
# --------------------------------------------------------------------------- #


def load_markets(
    n: int,
    *,
    parquet_path: Optional[Path] = None,
    seed: int = 42,
) -> list[MarketRecord]:
    """Load ``n`` resolved markets, or fall back to ``outputs/sample_*.json``.

    Falling back is intentional: the operator can run a smoke test
    before the full resolved-markets parquet has been generated upstream.
    """

    target = parquet_path or DEFAULT_RESOLVED_PARQUET
    if target.exists():
        try:
            import pandas as pd

            df = pd.read_parquet(target)
            # ``sample`` keeps determinism with the seed AND avoids
            # always grabbing the same prefix of the file.
            if len(df) > n:
                df = df.sample(n=n, random_state=seed)
            records = [MarketRecord.from_row(row) for row in df.to_dict(orient="records")]
            LOGGER.info("loaded n=%d markets from %s", len(records), target)
            return records
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("failed to load parquet %s; falling back to samples", target)

    LOGGER.warning(
        "resolved parquet missing at %s; falling back to outputs/sample_*.json", target
    )
    samples = _load_sample_fallback(DEFAULT_SAMPLE_GLOB, n=n)
    if not samples:
        raise FileNotFoundError(
            f"No resolved markets parquet at {target} and no outputs/sample_*.json fallback."
        )
    return samples


def _load_sample_fallback(samples_dir: Path, *, n: int) -> list[MarketRecord]:
    """Build mock ``MarketRecord``s from the legacy ``outputs/sample_*.json``."""

    records: list[MarketRecord] = []
    for idx, path in enumerate(sorted(samples_dir.glob("sample_*.json"))):
        if len(records) >= n:
            break
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:  # pragma: no cover - corrupt fixture
            continue
        # Synthesize a plausible outcome from alternating YES/NO so the
        # outcome-matcher branch is exercised.
        outcome = "YES" if idx % 2 == 0 else "NO"
        records.append(
            MarketRecord(
                market_id=f"sample-{idx}",
                question=str(data.get("title") or "Untitled sample"),
                category=str(data.get("category") or "sample"),
                outcome=outcome,
                total_volume_usdc=10_000.0 * (idx + 1),
                uma_dispute=False,
                resolution_source=str(data.get("resolution_source") or ""),
            )
        )
    return records


# --------------------------------------------------------------------------- #
# Core async driver.                                                          #
# --------------------------------------------------------------------------- #


async def _run_one_market(
    market: MarketRecord,
    *,
    rng: random.Random,
    llm_factory: LLMFactory,
    mock_llm: bool,
    use_embeddings: bool,
) -> BacktestResult:
    """Backtest a single market end-to-end."""

    event_dict = {
        "event_id": f"backtest-{market.market_id}",
        "title_zh": market.question,
        "body_zh": market.question,
        "topic": market.category,
        "url": market.resolution_source,
    }
    bids = _build_bid_table(event_dict)
    winner = _pick_winner(bids, rng=rng)
    question = await _run_agent_pipeline(
        winner, market, llm_factory=llm_factory, mock_llm=mock_llm
    )
    verdict = await _run_judges(
        question, market, llm_factory=llm_factory, mock_llm=mock_llm
    )
    comparison: OutcomeComparison = compare_questions(
        question.question_en,
        market.question,
        market.outcome,
        use_embeddings=use_embeddings,
    )
    roi: RoiEstimate = estimate_roi(
        market.total_volume_usdc,
        verdict.get("verdict", "FAIL"),
        float(verdict.get("overall_score", 0)),
    )

    style_passes = verdict.get("style_alignment_passes") or {}
    d5_passed: Optional[bool]
    if "d5" in style_passes:
        d5_passed = bool(style_passes["d5"])
    else:
        d5_passed = None

    notes_parts: list[str] = []
    if comparison.notes:
        notes_parts.append(comparison.notes)
    panel_notes = verdict.get("notes") or []
    if panel_notes:
        notes_parts.append("; ".join(str(n) for n in panel_notes)[:240])

    return BacktestResult(
        market_id=market.market_id,
        actual_question=market.question,
        actual_outcome=market.outcome,
        actual_volume=market.total_volume_usdc,
        agent_winner=winner,
        agent_winner_address=_AGENT_WALLET_STUBS.get(winner, "0x" + "0" * 40),
        agent_question=question.question_en,
        judge_verdict=str(verdict.get("verdict", "FAIL")),
        judge_score=float(verdict.get("overall_score", 0)),
        semantic_similarity=comparison.semantic_similarity,
        outcome_match=comparison.outcome_match,
        estimated_roi_usdc=roi.net_roi_usdc,
        uma_dispute=market.uma_dispute,
        category=market.category,
        notes=" | ".join(notes_parts),
        framing_predicted=comparison.framing_predicted,
        capture_rate=roi.capture_rate,
        builder_fee_usdc=roi.builder_fee_usdc,
        d5_passed=d5_passed,
        bids=bids,
    )


async def run_backtest_async(
    *,
    n: int = 100,
    seed: int = 42,
    output_dir: Optional[Path] = None,
    mock_llm: bool = True,
    use_embeddings: Optional[bool] = None,
    parquet_path: Optional[Path] = None,
    markets: Optional[Iterable[MarketRecord]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> dict[str, Any]:
    """Run the full backtest. Returns a summary dict.

    ``use_embeddings=None`` defaults to ``not mock_llm`` (mock runs skip
    the heavy sentence-transformers download for speed; real runs use
    it for proper semantic similarity).
    """

    rng = random.Random(seed)
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    if use_embeddings is None:
        use_embeddings = not mock_llm

    if markets is None:
        markets_list = load_markets(n, parquet_path=parquet_path, seed=seed)
    else:
        markets_list = list(markets)
    if not markets_list:
        raise RuntimeError("No markets to backtest.")

    llm_factory = make_backtest_llm_factory(markets_list[0], real=not mock_llm)

    results: list[BacktestResult] = []
    total = len(markets_list)
    for idx, market in enumerate(markets_list):
        if progress_callback is not None:
            progress_callback(idx, total)
        try:
            result = await _run_one_market(
                market,
                rng=rng,
                llm_factory=llm_factory,
                mock_llm=mock_llm,
                use_embeddings=use_embeddings,
            )
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("market %s failed; recording skeleton", market.market_id)
            result = BacktestResult(
                market_id=market.market_id,
                actual_question=market.question,
                actual_outcome=market.outcome,
                actual_volume=market.total_volume_usdc,
                agent_winner="",
                agent_winner_address="",
                agent_question="",
                judge_verdict="ERROR",
                judge_score=0.0,
                semantic_similarity=0.0,
                outcome_match=False,
                estimated_roi_usdc=0.0,
                uma_dispute=market.uma_dispute,
                category=market.category,
                notes=f"pipeline error: {exc!r}",
            )
        results.append(result)

    summary = _summarize(results)
    _write_artifacts(results, summary, output_dir=output_dir)
    return summary


def run_backtest(**kwargs: Any) -> dict[str, Any]:
    """Sync wrapper around :func:`run_backtest_async`."""

    return asyncio.run(run_backtest_async(**kwargs))


# --------------------------------------------------------------------------- #
# Summary + I/O helpers.                                                      #
# --------------------------------------------------------------------------- #


def _summarize(results: list[BacktestResult]) -> dict[str, Any]:
    """Compute aggregate stats for ``summary.json``."""

    n = len(results)
    if n == 0:
        return {"n_markets": 0}

    verdict_counts = {"PASS": 0, "FAIL": 0, "BORDERLINE": 0, "ERROR": 0}
    for r in results:
        verdict_counts[r.judge_verdict] = verdict_counts.get(r.judge_verdict, 0) + 1

    outcome_matches = sum(1 for r in results if r.outcome_match)
    similarity_total = sum(r.semantic_similarity for r in results)
    roi_total = sum(r.estimated_roi_usdc for r in results)

    # Per-category breakdown.
    per_category: dict[str, dict[str, Any]] = {}
    for r in results:
        bucket = per_category.setdefault(
            r.category or "other",
            {"n": 0, "matches": 0, "roi": 0.0, "passes": 0},
        )
        bucket["n"] += 1
        bucket["matches"] += int(r.outcome_match)
        bucket["roi"] += r.estimated_roi_usdc
        bucket["passes"] += int(r.judge_verdict == "PASS")
    for cat, data in per_category.items():
        n_cat = max(1, data["n"])
        data["accuracy"] = data["matches"] / n_cat
        data["pass_rate"] = data["passes"] / n_cat
        # Drop the intermediate counters that the report doesn't need.
        data.pop("matches")
        data.pop("passes")

    # D5 dispute-detection scorecard.
    uma_total = sum(1 for r in results if r.uma_dispute)
    uma_caught_by_d5 = sum(
        1 for r in results if r.uma_dispute and r.d5_passed is False
    )
    uma_missed_by_d5 = sum(
        1 for r in results if r.uma_dispute and r.d5_passed is True
    )

    return {
        "n_markets": n,
        "n_PASS": verdict_counts.get("PASS", 0),
        "n_FAIL": verdict_counts.get("FAIL", 0),
        "n_BORDERLINE": verdict_counts.get("BORDERLINE", 0),
        "n_ERROR": verdict_counts.get("ERROR", 0),
        "outcome_accuracy": outcome_matches / n,
        "semantic_similarity_avg": similarity_total / n,
        "estimated_total_roi_usdc": roi_total,
        "per_category": per_category,
        "uma_dispute_total": uma_total,
        "uma_dispute_caught_by_D5": uma_caught_by_d5,
        "uma_dispute_missed_by_D5": uma_missed_by_d5,
    }


def _write_artifacts(
    results: list[BacktestResult],
    summary: dict[str, Any],
    *,
    output_dir: Path,
) -> None:
    """Persist per-market JSONL, summary JSON, and Markdown report."""

    from .reporter import generate_report

    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "per_market_results.jsonl"
    summary_path = output_dir / "summary.json"
    report_path = output_dir / "backtest_report.md"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r.as_dict(), ensure_ascii=False) + "\n")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path.write_text(generate_report(results, summary), encoding="utf-8")
    LOGGER.info(
        "wrote backtest artifacts: %s, %s, %s",
        jsonl_path,
        summary_path,
        report_path,
    )
