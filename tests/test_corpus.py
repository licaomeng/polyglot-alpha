"""Tests for the corpus subpackage.

Network access is fully mocked via ``unittest.mock.patch`` on
``requests.Session.get`` — the fixture in ``tests/fixtures/`` plays the
role of a Gamma API response.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from polyglot_alpha.corpus import (
    Lookup,
    SimilarHit,
    classify_pattern,
    summarize_patterns,
)
from polyglot_alpha.corpus import embed as embed_module
from polyglot_alpha.corpus import few_shots as few_shots_module
from polyglot_alpha.corpus import pattern_analysis as pattern_module
from polyglot_alpha.corpus import resolved_analysis as resolved_analysis_module
from polyglot_alpha.corpus import resolved_scraper as resolved_scraper_module
from polyglot_alpha.corpus import scraper as scraper_module
from polyglot_alpha.corpus import style_guide as style_guide_module

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gamma_events_page.json"


# --------------------------------------------------------------------------- #
# Helpers.                                                                    #
# --------------------------------------------------------------------------- #


def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


class _StubEncoder:
    """Deterministic, dependency-free embedding stub.

    Each text becomes a 384-dim vector derived from its hash modulo a
    small prime; the same text always yields the same vector and the
    vector is unit-normalized to match the real encoder's contract.
    """

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def encode(
        self,
        texts,
        *,
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,
        show_progress_bar: bool = False,
        batch_size: int = 32,
    ):
        vectors = []
        for t in texts:
            rng = np.random.default_rng(seed=abs(hash(t)) % (2**32))
            v = rng.normal(size=self.dim).astype("float32")
            if normalize_embeddings:
                n = np.linalg.norm(v) or 1.0
                v = v / n
            vectors.append(v)
        arr = np.stack(vectors).astype("float32")
        return arr


# --------------------------------------------------------------------------- #
# Test 1 — scraper normalization with mocked HTTP.                            #
# --------------------------------------------------------------------------- #


def test_scraper_flattens_events_and_filters_multi_outcome(tmp_path: Path) -> None:
    page = _load_fixture()
    # Two pages: real fixture, then empty list to terminate the crawl.
    mock_resp_full = MagicMock(status_code=200)
    mock_resp_full.json.return_value = page
    mock_resp_full.raise_for_status.return_value = None

    mock_resp_empty = MagicMock(status_code=200)
    mock_resp_empty.json.return_value = []
    mock_resp_empty.raise_for_status.return_value = None

    with patch.object(
        scraper_module.requests.Session,
        "get",
        side_effect=[
            mock_resp_full,
            mock_resp_empty,
            mock_resp_empty,
            mock_resp_empty,
            mock_resp_empty,
        ],
    ):
        rows = scraper_module.scrape_polymarket(
            target_rows=1000, page_size=100, include_closed=False
        )

    questions = {r.question for r in rows}
    assert "Will Bitcoin be above $200,000 by December 31, 2026?" in questions
    assert "Which team will win MVP this season?" not in questions, (
        "Multi-outcome markets must be filtered out"
    )
    # Categories propagate from event.tags or event.category.
    btc_row = next(
        r
        for r in rows
        if r.question.startswith("Will Bitcoin be above $200,000")
    )
    assert btc_row.category == "Crypto"
    assert btc_row.market_id == "m-9001-1"

    # Round-trip through parquet.
    out = scraper_module.save_parquet(rows, tmp_path / "corpus.parquet")
    df = pd.read_parquet(out)
    assert len(df) == len(rows)
    assert set(["market_id", "question", "category"]).issubset(df.columns)


# --------------------------------------------------------------------------- #
# Test 2 — pattern classification.                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Will Bitcoin be above $200,000 by December 31, 2026?", "P3"),
        ("Will the Fed cut rates by July 31?", "P1"),
        ("Who will be the next US President?", "P4"),
        ("How many SpaceX launches by Dec 31, 2026?", "P6"),
        (
            "Will the Fed cut rates between July 1 and September 30 2026?",
            "P5",
        ),
        ("2028 GOP Nominee?", "P2"),
        ("Next President of France?", "P2"),
        ("This is not a question", "OTHER"),
    ],
)
def test_classify_pattern(question: str, expected: str) -> None:
    assert classify_pattern(question) == expected


def test_summarize_patterns_produces_percentages() -> None:
    labels = ["P1"] * 3 + ["P2"] * 1 + ["OTHER"] * 1
    stats = summarize_patterns(labels)
    pcts = stats.percentages()
    assert stats.total == 5
    assert stats.counts["P1"] == 3
    assert pcts["P1"] == pytest.approx(60.0)
    report = pattern_module.stats_to_report(stats)
    assert "Polymarket Question Framing Patterns" in report
    assert "60.0%" in report


# --------------------------------------------------------------------------- #
# Test 3 — embed + FAISS round trip.                                          #
# --------------------------------------------------------------------------- #


def test_embed_and_index_round_trip(tmp_path: Path) -> None:
    df = pd.DataFrame(
        [
            {
                "market_id": "m1",
                "question": "Will BTC reach 200k by end of 2026?",
                "category": "Crypto",
            },
            {
                "market_id": "m2",
                "question": "Will Argentina win the 2026 FIFA World Cup?",
                "category": "Sports",
            },
            {
                "market_id": "m3",
                "question": "Who will be the next US President?",
                "category": "Politics",
            },
        ]
    )
    parquet_path = tmp_path / "questions.parquet"
    df.to_parquet(parquet_path, index=False)

    index_path = tmp_path / "idx.faiss"
    meta_path = tmp_path / "idx_meta.json"

    encoder = _StubEncoder()
    embed_module.build_corpus_index(
        parquet_path,
        index_path=index_path,
        meta_path=meta_path,
        model=encoder,
    )
    assert index_path.exists()
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert len(meta["records"]) == 3
    assert meta["records"][0]["market_id"] == "m1"


# --------------------------------------------------------------------------- #
# Test 4 — Lookup.find_similar returns sensible results.                      #
# --------------------------------------------------------------------------- #


def test_find_similar_returns_self_as_best_match() -> None:
    questions = [
        ("m1", "Will BTC reach 200k by end of 2026?", "Crypto"),
        ("m2", "Will Argentina win the 2026 FIFA World Cup?", "Sports"),
        ("m3", "Who will be the next US President?", "Politics"),
    ]
    encoder = _StubEncoder()
    texts = [q for _, q, _ in questions]
    embeddings = embed_module.embed_texts(texts, model=encoder)
    index = embed_module.build_faiss_index(embeddings)
    meta_records = [
        {"idx": i, "market_id": mid, "question": q, "category": cat}
        for i, (mid, q, cat) in enumerate(questions)
    ]
    lookup = Lookup.from_components(index, meta_records, encoder)

    hits = lookup.find_similar(
        "Will BTC reach 200k by end of 2026?", k=2
    )
    assert len(hits) == 2
    assert isinstance(hits[0], SimilarHit)
    assert hits[0].market_id == "m1", (
        "Exact-match query should retrieve itself as the top neighbour"
    )
    # Cosine similarity of an exact match against a unit-normalized
    # vector is ~1.0; the score must be at least notably higher than
    # the runner-up since hashes of distinct strings collide rarely.
    assert hits[0].score >= hits[1].score
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)


def test_find_similar_clamps_k_and_handles_empty_query() -> None:
    encoder = _StubEncoder()
    embeddings = embed_module.embed_texts(["only one question"], model=encoder)
    index = embed_module.build_faiss_index(embeddings)
    lookup = Lookup.from_components(
        index,
        [{"market_id": "m1", "question": "only one question", "category": "X"}],
        encoder,
    )
    # Requesting k=10 against a 1-row index must clamp, not crash.
    hits = lookup.find_similar("only one question", k=10)
    assert len(hits) == 1
    # Empty query returns no hits.
    assert lookup.find_similar("   ", k=3) == []


# --------------------------------------------------------------------------- #
# Test 5 — few-shots diversity.                                               #
# --------------------------------------------------------------------------- #


def test_build_few_shots_diversifies_categories(tmp_path: Path) -> None:
    rows = []
    # 6 categories x 4 questions each -> 24 candidate rows.
    cats = ["politics", "sports", "crypto", "geopolitics", "entertainment", "weather"]
    for cat_idx, cat in enumerate(cats):
        for j in range(4):
            rows.append(
                {
                    "market_id": f"m-{cat_idx}-{j}",
                    "question": f"Will {cat} event {j} happen by 2027?",
                    "category": cat,
                    "resolution_criteria": "Resolves YES if ...",
                    "volume_usd": (cat_idx + 1) * 100 + (3 - j),
                }
            )
    df = pd.DataFrame(rows)
    few = few_shots_module.build_few_shots(df, target_count=12)
    assert len(few) == 12
    seen_cats = {fs.category for fs in few}
    assert len(seen_cats) >= 5, (
        f"Expected at least 5 distinct categories in 12 picks, got {seen_cats}"
    )
    # First-round picks should be the highest-volume row in each bucket.
    expected_first_titles = {
        f"Will {cat} event 0 happen by 2027?" for cat in cats
    }
    actual_titles = {fs.title for fs in few[: len(cats)]}
    assert actual_titles == expected_first_titles

    # Saved file round-trips as valid JSON.
    out = few_shots_module.save_few_shots(few, tmp_path / "few.json")
    payload = json.loads(out.read_text())
    assert payload["count"] == 12
    assert len(payload["examples"]) == 12


# --------------------------------------------------------------------------- #
# Test 6 — style guide distillation with a stub LLM.                          #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Test 7 — resolved-scraper outcome classifier + dispute detection.            #
# --------------------------------------------------------------------------- #


def _resolved_market(**overrides):
    base = {
        "id": "42",
        "question": "Will BTC be above $200k by EOY?",
        "closed": True,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["1", "0"]',
        "umaResolutionStatuses": '["proposed", "resolved"]',
        "category": "",
        "volumeNum": 1500.0,
        "closedTime": "2026-01-01 00:00:00+00",
        "createdAt": "2025-12-01T00:00:00Z",
        "endDate": "2026-01-01T00:00:00Z",
        "resolutionSource": "https://example.com",
        "events": [{"title": "BTC price markets", "category": None}],
    }
    base.update(overrides)
    return base


def test_resolved_scraper_classifies_yes_no_disputed_refunded() -> None:
    yes_row = resolved_scraper_module.market_to_row(_resolved_market())
    assert yes_row is not None
    assert yes_row.outcome == "YES"
    assert yes_row.uma_dispute is False
    # Category is keyword-derived from question/event title ("btc" -> Crypto).
    assert yes_row.category == "Crypto"

    no_row = resolved_scraper_module.market_to_row(
        _resolved_market(outcomePrices='["0", "1"]')
    )
    assert no_row.outcome == "NO"

    disputed_row = resolved_scraper_module.market_to_row(
        _resolved_market(
            umaResolutionStatuses='["proposed", "disputed", "proposed", "resolved"]',
            outcomePrices='["1", "0"]',
        )
    )
    assert disputed_row.outcome == "YES"  # outcome still YES, prices are clean
    assert disputed_row.uma_dispute is True  # but dispute flag set

    refunded_row = resolved_scraper_module.market_to_row(
        _resolved_market(outcomePrices='["0", "0"]')
    )
    assert refunded_row.outcome == "REFUNDED"


def test_resolved_scraper_skips_non_binary_and_open() -> None:
    multi = resolved_scraper_module.market_to_row(
        _resolved_market(outcomes='["A","B","C"]', outcomePrices='["0.4","0.3","0.3"]')
    )
    assert multi is None

    open_market = resolved_scraper_module.market_to_row(_resolved_market(closed=False))
    assert open_market is None


def test_resolved_analysis_distribution_and_markdown() -> None:
    df = pd.DataFrame(
        [
            {
                "market_id": "1",
                "question": "Will Trump win the election?",
                "category": "Politics",
                "created_at": "2024-01-01",
                "end_date": "2024-11-05",
                "resolved_at": "2024-11-06",
                "outcome": "YES",
                "outcome_prices": [1.0, 0.0],
                "total_volume_usdc": 5_000_000.0,
                "uma_dispute": False,
                "resolution_source": "",
                "winning_outcome": "Yes",
            },
            {
                "market_id": "2",
                "question": "Will the Fed cut by June?",
                "category": "Economics",
                "created_at": "2024-03-01",
                "end_date": "2024-06-30",
                "resolved_at": "2024-07-01",
                "outcome": "NO",
                "outcome_prices": [0.0, 1.0],
                "total_volume_usdc": 50_000.0,
                "uma_dispute": True,
                "resolution_source": "",
                "winning_outcome": "No",
            },
            {
                "market_id": "3",
                "question": "Will it rain?",
                "category": "Weather",
                "created_at": "2024-05-01",
                "end_date": "2024-05-02",
                "resolved_at": "2024-05-03",
                "outcome": "DISPUTED",
                "outcome_prices": [0.0, 0.0],
                "total_volume_usdc": 100.0,
                "uma_dispute": True,
                "resolution_source": "",
                "winning_outcome": None,
            },
        ]
    )
    dist = resolved_analysis_module.compute_distribution(df)
    assert dist["total_markets"] == 3
    assert dist["yes_rate_overall"] == pytest.approx(1 / 3)
    assert dist["no_rate_overall"] == pytest.approx(1 / 3)
    assert dist["disputed_rate_overall"] == pytest.approx(1 / 3)
    assert dist["uma_dispute_rate_overall"] == pytest.approx(2 / 3)
    assert dist["by_category"]["Politics"]["yes"] == 1
    assert dist["by_category"]["Economics"]["no"] == 1
    assert dist["by_volume_tier"]["high"]["total"] == 1
    assert dist["by_volume_tier"]["mid"]["total"] == 1
    assert dist["by_volume_tier"]["low"]["total"] == 1

    md = resolved_analysis_module.build_summary_markdown(df, dist)
    assert "# Polymarket Resolved Markets" in md
    assert "Total markets" in md
    # Top-volume table should include the high-volume market.
    assert "Will Trump win the election" in md


# --------------------------------------------------------------------------- #
# Test 8 — style guide distillation with a stub LLM.                          #
# --------------------------------------------------------------------------- #


def test_distill_style_guide_uses_llm_stub() -> None:
    df = pd.DataFrame(
        [
            {"question": "Will A by 2026?", "category": "Crypto"},
            {"question": "Who will be the next mayor?", "category": "Politics"},
            {"question": "How many launches by EOY?", "category": "Tech"},
            {"question": "Will it rain in NYC on July 4?", "category": "Weather"},
        ]
    )
    captured: dict = {}

    async def fake_complete(prompt: str, *, system=None) -> str:
        captured["prompt"] = prompt
        captured["system"] = system
        return (
            "```markdown\n"
            "# Polymarket Question Style Guide\n"
            "- Be concise.\n"
            "```\n"
        )

    md = asyncio.run(
        style_guide_module.distill_style_guide(
            df, sample_size=4, llm_complete=fake_complete
        )
    )
    assert "# Polymarket Question Style Guide" in md
    assert "```" not in md, "Markdown fences must be stripped"
    assert "Be concise." in md
    # The prompt contains numbered question samples.
    assert "1." in captured["prompt"]
    assert captured["system"]
