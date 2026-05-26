"""Tests for the backtest framework.

The mock-LLM path is exercised end-to-end so the test suite stays fast
(<5s) and offline. Real-LLM behaviour is asserted indirectly via the
LLM-factory swap test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polyglot_alpha.backtest.outcome_matcher import (
    OutcomeComparison,
    compare_questions,
    infer_category,
    infer_framing,
)
from polyglot_alpha.backtest.roi_estimator import (
    BUILDER_FEE_BPS,
    CAPTURE_RATE_FAIL,
    CAPTURE_RATE_PASS,
    CAPTURE_RATE_PASS_HIGH,
    HIGH_CONFIDENCE_THRESHOLD,
    estimate_roi,
)
from polyglot_alpha.backtest.runner import (
    MarketRecord,
    _pick_winner,
    load_markets,
    run_backtest,
)


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def mock_markets() -> list[MarketRecord]:
    """Three deterministic market records spanning YES / NO / dispute."""

    return [
        MarketRecord(
            market_id="bt-1",
            question="Will Bitcoin exceed $100k by 2026-12-31?",
            category="crypto",
            outcome="YES",
            total_volume_usdc=50_000.0,
            uma_dispute=False,
            resolution_source="https://example.com/btc",
        ),
        MarketRecord(
            market_id="bt-2",
            question="Will the Fed cut rates in March 2026?",
            category="economics",
            outcome="NO",
            total_volume_usdc=20_000.0,
            uma_dispute=True,
            resolution_source="https://example.com/fed",
        ),
        MarketRecord(
            market_id="bt-3",
            question="Will Apple announce a foldable iPhone before 2026-12-31?",
            category="tech",
            outcome="NO",
            total_volume_usdc=8_000.0,
            uma_dispute=False,
            resolution_source="https://example.com/aapl",
        ),
    ]


# --------------------------------------------------------------------------- #
# ROI estimator                                                               #
# --------------------------------------------------------------------------- #


class TestRoiEstimator:
    def test_pass_high_confidence_uses_top_capture_rate(self) -> None:
        roi = estimate_roi(100_000.0, "PASS", HIGH_CONFIDENCE_THRESHOLD)
        assert roi.capture_rate == pytest.approx(CAPTURE_RATE_PASS_HIGH)
        expected_fee = 100_000.0 * CAPTURE_RATE_PASS_HIGH * (BUILDER_FEE_BPS / 10_000.0)
        assert roi.builder_fee_usdc == pytest.approx(expected_fee)
        # Net = builder_fee - agent_cost; should still be positive on a 100k market.
        assert roi.net_roi_usdc > 0

    def test_pass_normal_uses_lower_capture_rate(self) -> None:
        roi = estimate_roi(100_000.0, "PASS", HIGH_CONFIDENCE_THRESHOLD - 1)
        assert roi.capture_rate == pytest.approx(CAPTURE_RATE_PASS)

    def test_fail_returns_zero_fee(self) -> None:
        roi = estimate_roi(1_000_000.0, "FAIL", 0)
        assert roi.capture_rate == pytest.approx(CAPTURE_RATE_FAIL)
        assert roi.builder_fee_usdc == 0.0
        # Net negative because of agent_cost stub.
        assert roi.net_roi_usdc < 0

    def test_zero_volume_returns_zero(self) -> None:
        roi = estimate_roi(0.0, "PASS", 95.0)
        assert roi.builder_fee_usdc == 0.0

    def test_negative_volume_clamped_to_zero(self) -> None:
        roi = estimate_roi(-500.0, "PASS", 95.0)
        assert roi.builder_fee_usdc == 0.0


# --------------------------------------------------------------------------- #
# Outcome matcher                                                             #
# --------------------------------------------------------------------------- #


class TestOutcomeMatcher:
    def test_identical_questions_match_with_jaccard(self) -> None:
        result: OutcomeComparison = compare_questions(
            "Will Bitcoin exceed $100k by 2026-12-31?",
            "Will Bitcoin exceed $100k by 2026-12-31?",
            "YES",
            use_embeddings=False,
        )
        assert result.semantic_similarity == pytest.approx(1.0)
        assert result.semantic_match is True
        assert result.framing_predicted == "YES"
        assert result.outcome_match is True

    def test_disjoint_questions_low_similarity(self) -> None:
        result = compare_questions(
            "Will Apple ship a foldable iPhone?",
            "Will the Fed cut interest rates?",
            "NO",
            use_embeddings=False,
        )
        assert result.semantic_similarity < 0.3

    def test_framing_yes_matches_yes_outcome(self) -> None:
        result = compare_questions(
            "Will the policy be announced before December?",
            "Will the policy be announced before December?",
            "YES",
            use_embeddings=False,
        )
        assert result.framing_predicted == "YES"
        assert result.outcome_match is True

    def test_framing_yes_misses_on_no_resolution(self) -> None:
        result = compare_questions(
            "Will Apple announce a foldable iPhone?",
            "Will Apple announce a foldable iPhone?",
            "NO",
            use_embeddings=False,
        )
        # Question framing is YES, actual is NO → miss.
        assert result.framing_predicted == "YES"
        assert result.outcome_match is False

    def test_non_binary_outcome_is_not_matched(self) -> None:
        result = compare_questions(
            "Will Verstappen win the race?",
            "Race winner?",
            "Verstappen",
            use_embeddings=False,
        )
        assert result.outcome_match is False
        assert "non-binary" in result.notes

    def test_infer_framing_yes(self) -> None:
        assert infer_framing("Will X reach 100 by year-end?") == "YES"

    def test_infer_framing_no(self) -> None:
        # "Below" should mark this as a NO-framing.
        assert infer_framing("Will X fail to stay below the limit?") == "NO"

    def test_infer_framing_unknown(self) -> None:
        assert infer_framing("xyz") == "UNKNOWN"

    def test_infer_category_crypto(self) -> None:
        assert infer_category("Will Bitcoin reach $100k?") == "crypto"

    def test_infer_category_other(self) -> None:
        assert infer_category("Random unrelated string") == "other"


# --------------------------------------------------------------------------- #
# Auction logic                                                               #
# --------------------------------------------------------------------------- #


class TestAuction:
    def test_pick_winner_picks_lowest_bid(self) -> None:
        import random as _random

        rng = _random.Random(0)
        bids = {"gemini": 0.30, "deepseek": 0.75, "qwen": 0.40}
        assert _pick_winner(bids, rng=rng) == "gemini"

    def test_pick_winner_handles_ties_deterministically(self) -> None:
        import random as _random

        rng = _random.Random(123)
        bids = {"a": 0.5, "b": 0.5, "c": 0.5}
        first = _pick_winner(bids, rng=rng)
        # With the same seed we get the same answer.
        rng = _random.Random(123)
        second = _pick_winner(bids, rng=rng)
        assert first == second


# --------------------------------------------------------------------------- #
# Market loader                                                               #
# --------------------------------------------------------------------------- #


class TestLoadMarkets:
    def test_loads_from_real_parquet_if_available(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        parquet = repo_root / "corpus" / "polymarket_resolved.parquet"
        if not parquet.exists():
            pytest.skip("resolved markets parquet not present in this checkout")
        markets = load_markets(n=3, parquet_path=parquet, seed=42)
        assert len(markets) == 3
        assert all(isinstance(m, MarketRecord) for m in markets)
        assert all(m.question for m in markets)

    def test_falls_back_to_sample_json(self, tmp_path: Path) -> None:
        # Point at a non-existent parquet so the loader uses sample_*.json
        # via the default ``outputs/`` directory.
        markets = load_markets(n=2, parquet_path=tmp_path / "missing.parquet", seed=42)
        assert len(markets) >= 1
        assert all(isinstance(m, MarketRecord) for m in markets)


# --------------------------------------------------------------------------- #
# End-to-end smoke test (mock LLM)                                            #
# --------------------------------------------------------------------------- #


class TestRunBacktestSmoke:
    def test_full_pipeline_with_mock_llm(
        self,
        mock_markets: list[MarketRecord],
        tmp_path: Path,
    ) -> None:
        import asyncio

        from polyglot_alpha.backtest.runner import run_backtest_async

        summary = asyncio.run(
            run_backtest_async(
                n=len(mock_markets),
                seed=42,
                output_dir=tmp_path,
                mock_llm=True,
                use_embeddings=False,  # avoid the sentence-transformers download
                markets=mock_markets,
            )
        )
        assert summary["n_markets"] == len(mock_markets)
        # Output files should have landed in tmp_path.
        jsonl = tmp_path / "per_market_results.jsonl"
        summary_path = tmp_path / "summary.json"
        report_path = tmp_path / "backtest_report.md"
        assert jsonl.exists()
        assert summary_path.exists()
        assert report_path.exists()

        # Re-read JSONL and confirm one row per market.
        rows = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
        assert len(rows) == len(mock_markets)
        row0 = rows[0]
        # Sanity-check required fields per the spec.
        for key in (
            "market_id",
            "actual_question",
            "actual_outcome",
            "actual_volume",
            "agent_winner",
            "agent_question",
            "judge_verdict",
            "judge_score",
            "semantic_similarity",
            "outcome_match",
            "estimated_roi_usdc",
            "uma_dispute",
            "category",
            "notes",
        ):
            assert key in row0, f"missing key {key} in row"

        # Markdown report is non-empty and labelled.
        report_text = report_path.read_text()
        assert "PolyglotAlpha v2 Backtest Report" in report_text
        assert "Executive summary" in report_text

    def test_run_backtest_sync_wrapper(
        self,
        mock_markets: list[MarketRecord],
        tmp_path: Path,
    ) -> None:
        summary = run_backtest(
            n=len(mock_markets),
            seed=99,
            output_dir=tmp_path,
            mock_llm=True,
            use_embeddings=False,
            markets=mock_markets,
        )
        assert summary["n_markets"] == len(mock_markets)
        assert "outcome_accuracy" in summary
        assert "estimated_total_roi_usdc" in summary
