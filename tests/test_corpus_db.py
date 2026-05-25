"""Tests for the corpus DB ingestion pipeline + lookup API.

Covers:
    * Schema registration for the 5 new tables.
    * Idempotent upsert behavior for corpus_markets.
    * Few-shots / style-guide / reference ingestion.
    * FAISS reconcile (embedding_idx linkage).
    * CorpusLookup wrappers (similar / few-shots / style / reference /
      search_resolved_markets / pattern_priors).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from polyglot_alpha.corpus.db_ingestion import (
    IngestStats,
    ingest_corpus_markets,
    ingest_few_shots,
    ingest_reference_translations,
    ingest_style_guide,
    reconcile_with_faiss,
)
from polyglot_alpha.corpus.lookup import Lookup, SimilarHit
from polyglot_alpha.corpus.lookup_db import (
    CorpusLookup,
    EnrichedSimilarHit,
)
from polyglot_alpha.corpus.reference_loader import (
    get_reference,
    list_references,
    load_references,
)
from polyglot_alpha.persistence import session_scope
from polyglot_alpha.persistence.models import (
    BacktestResult,
    CorpusMarket,
    CorpusMarketState,
    FewShotExemplar,
    FewShotRole,
    ReferenceTranslation,
    StyleRule,
)


# --------------------------------------------------------------------------- #
# Fixtures.                                                                   #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def markets_parquet(tmp_path: Path) -> Path:
    """A minimal parquet that exercises both 'questions' + 'resolved' columns."""

    df = pd.DataFrame(
        [
            {
                "market_id": "m-1",
                "question": "Will BTC top $100K by 2026?",
                "category": "Crypto",
                "tags": json.dumps(["Crypto", "BTC"]),
                "resolution_date": "2026-12-31T23:59:59Z",
                "volume_usd": 1_500_000.0,
                "closed": True,
            },
            {
                "market_id": "m-2",
                "question": "Will the Fed cut rates by July 2026?",
                "category": "Economy",
                "tags": json.dumps(["Economy"]),
                "resolution_date": "2026-07-31T23:59:59Z",
                "volume_usd": 250_000.0,
                "closed": False,
            },
            {
                # Resolved-corpus shape.
                "market_id": "m-3",
                "question": "Did Espresso FDV top $700M one day after launch?",
                "category": "Crypto",
                "created_at": "2026-02-10T00:40:53.39661Z",
                "end_date": "2028-01-01T05:00:00Z",
                "resolved_at": "2026-02-14T00:09:43Z",
                "outcome": "NO",
                "outcome_prices": [0.0, 1.0],
                "total_volume_usdc": 42_124.5,
                "uma_dispute": False,
                "resolution_source": "",
            },
        ]
    )
    path = tmp_path / "markets.parquet"
    df.to_parquet(path)
    return path


@pytest.fixture()
def few_shots_json(tmp_path: Path) -> Path:
    payload = {
        "version": 1,
        "count": 2,
        "examples": [
            {
                "title": "Will Byron Donalds win the 2028 Republican nomination?",
                "category": "Politics",
                "resolution_criteria": "Resolves YES if ...",
                "why_good_exemplar": "Canonical P1 single-actor binary.",
                "market_id": "561986",
            },
            {
                "title": "MicroStrategy sells any Bitcoin in 2025?",
                "category": "Economy",
                "resolution_criteria": "Resolves YES if ...",
                "why_good_exemplar": "Compact P1 with year scope.",
                "market_id": "516926",
                "judge_dimension": "D5",
                "role": "POSITIVE_EXAMPLE",
            },
        ],
    }
    path = tmp_path / "few_shots.json"
    path.write_text(json.dumps(payload))
    return path


@pytest.fixture()
def style_guide_md(tmp_path: Path) -> Path:
    md = (
        "# Polymarket Question Style Guide\n\n"
        "- **Structure**: Clear declarative sentence, 15-20 words.\n"
        "- **Resolution clarity**: Definitive YES/NO outcome.\n"
        "- **Granularity**: Precise dates and thresholds.\n"
        "- **Leading-question avoidance**: No loaded language (D7 guard).\n"
    )
    path = tmp_path / "style_guide.md"
    path.write_text(md)
    return path


@pytest.fixture()
def reference_dir(tmp_path: Path) -> Path:
    ref_dir = tmp_path / "ground_truth"
    ref_dir.mkdir()
    ref_dir.joinpath("sample_0.json").write_text(
        json.dumps(
            {
                "sample_id": 0,
                "source_chinese": "央行行长……",
                "ground_truth_translation": {
                    "primary": "Will the PBOC cut the RRR by August 23, 2026?",
                    "alternative_phrasings": [
                        "Will the People's Bank of China cut the RRR by August 23, 2026?",
                    ],
                },
                "k5_framing_variants": [
                    "Will the PBOC cut the RRR by August 23, 2026?",
                    "Will China's central bank reduce the RRR by August 23, 2026?",
                ],
                "expected_bleu_threshold": 30,
                "expected_comet_threshold": 0.65,
                "polymarket_shape_validation": {
                    "structural_pattern": "P1_will_by_date",
                    "resolution_date": "2026-08-23",
                },
                "annotator_notes": "Solid agent translation.",
            }
        )
    )
    return ref_dir


@pytest.fixture()
def index_meta_json(tmp_path: Path) -> Path:
    payload = {
        "records": [
            {"idx": 0, "market_id": "m-1", "question": "Will BTC top $100K by 2026?", "category": "Crypto"},
            {"idx": 1, "market_id": "m-2", "question": "Will the Fed cut rates by July 2026?", "category": "Economy"},
            {"idx": 2, "market_id": "m-3", "question": "Did Espresso FDV top $700M?", "category": "Crypto"},
        ]
    }
    path = tmp_path / "index_meta.json"
    path.write_text(json.dumps(payload))
    return path


# --------------------------------------------------------------------------- #
# Schema registration.                                                        #
# --------------------------------------------------------------------------- #


def test_five_new_tables_registered(isolated_db: str) -> None:
    """All five new tables must be created by init_db()."""

    from sqlalchemy import inspect

    from polyglot_alpha.persistence import db as persistence_db

    insp = inspect(persistence_db.engine)
    names = set(insp.get_table_names())
    expected = {
        "corpus_markets",
        "few_shot_exemplars",
        "style_rules",
        "reference_translations",
        "backtest_results",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


# --------------------------------------------------------------------------- #
# Ingestion — corpus markets (idempotent upsert).                              #
# --------------------------------------------------------------------------- #


def test_ingest_corpus_markets_inserts_rows(
    isolated_db: str, markets_parquet: Path
) -> None:
    from sqlalchemy import select

    stats = asyncio.run(ingest_corpus_markets(markets_parquet, batch_size=2))
    assert stats.inserted == 3
    assert stats.updated == 0
    with session_scope() as session:
        rows = list(session.execute(select(CorpusMarket)).scalars())
        assert len(rows) == 3
        by_id = {r.market_id: r for r in rows}
        # m-1 was closed=True with no resolved_at -> state=closed.
        assert by_id["m-1"].state == CorpusMarketState.CLOSED.value
        # m-3 has resolved_at -> state=resolved + outcome=NO.
        assert by_id["m-3"].state == CorpusMarketState.RESOLVED.value
        assert by_id["m-3"].outcome == "NO"
        # tags parsed from JSON-encoded string.
        assert by_id["m-1"].tags == ["Crypto", "BTC"]
        # outcome_prices preserved.
        assert by_id["m-3"].outcome_prices == [0.0, 1.0]


def test_ingest_corpus_markets_is_idempotent(
    isolated_db: str, markets_parquet: Path
) -> None:
    first = asyncio.run(ingest_corpus_markets(markets_parquet))
    second = asyncio.run(ingest_corpus_markets(markets_parquet))
    from sqlalchemy import func, select

    assert first.inserted == 3
    assert second.inserted == 0
    assert second.updated == 3
    with session_scope() as session:
        count = session.execute(select(func.count()).select_from(CorpusMarket)).scalar_one()
        assert count == 3


def test_ingest_corpus_markets_skips_invalid_rows(
    isolated_db: str, tmp_path: Path
) -> None:
    df = pd.DataFrame(
        [
            {"market_id": None, "question": "no id"},
            {"market_id": "m-empty", "question": ""},
            {"market_id": "m-ok", "question": "Will X by Y?"},
        ]
    )
    path = tmp_path / "bad.parquet"
    df.to_parquet(path)
    stats = asyncio.run(ingest_corpus_markets(path))
    assert stats.inserted == 1
    assert stats.skipped == 2


# --------------------------------------------------------------------------- #
# Ingestion — few-shots, style rules, references.                              #
# --------------------------------------------------------------------------- #


def test_ingest_few_shots_with_default_dimension(
    isolated_db: str, few_shots_json: Path
) -> None:
    from sqlalchemy import select

    stats = asyncio.run(ingest_few_shots(few_shots_json, default_dimension="D2"))
    assert stats.inserted == 2
    with session_scope() as session:
        rows = list(session.execute(select(FewShotExemplar)).scalars())
        by_market = {r.market_id: r for r in rows}
        # First exemplar inherits default D2.
        assert by_market["561986"].judge_dimension == "D2"
        # Second exemplar overrides to D5.
        assert by_market["516926"].judge_dimension == "D5"
        assert by_market["516926"].role == FewShotRole.POSITIVE_EXAMPLE.value

    # Re-running is idempotent.
    second = asyncio.run(ingest_few_shots(few_shots_json, default_dimension="D2"))
    assert second.inserted == 0


def test_ingest_style_guide_extracts_bullets(
    isolated_db: str, style_guide_md: Path
) -> None:
    from sqlalchemy import select

    stats = asyncio.run(ingest_style_guide(style_guide_md))
    assert stats.inserted == 4
    with session_scope() as session:
        rows = list(session.execute(select(StyleRule)).scalars())
        texts = [r.rule_text for r in rows]
        assert any("Structure" in t for t in texts)
        # D7 mention should be auto-tagged.
        d7 = [r for r in rows if r.dimension == "D7"]
        assert len(d7) == 1


def test_ingest_reference_translations_directory(
    isolated_db: str, reference_dir: Path
) -> None:
    stats = asyncio.run(ingest_reference_translations(reference_dir))
    assert stats.inserted == 1
    ref = get_reference(0)
    assert ref is not None
    assert ref.primary_translation.startswith("Will the PBOC")
    assert len(ref.k5_framing_variants) == 2
    assert ref.expected_bleu_threshold == pytest.approx(30.0)
    assert ref.polymarket_shape_validation["structural_pattern"] == "P1_will_by_date"

    # Update-in-place when re-ingesting a modified file.
    reference_dir.joinpath("sample_0.json").write_text(
        json.dumps(
            {
                "sample_id": 0,
                "source_chinese": "updated",
                "ground_truth_translation": {
                    "primary": "Updated translation.",
                    "alternative_phrasings": [],
                },
                "k5_framing_variants": [],
            }
        )
    )
    second = asyncio.run(ingest_reference_translations(reference_dir))
    assert second.updated == 1
    updated = get_reference(0)
    assert updated.primary_translation == "Updated translation."


def test_reference_loader_helper_round_trip(
    isolated_db: str, reference_dir: Path
) -> None:
    stats = asyncio.run(load_references(reference_dir, ensure_schema=False))
    assert isinstance(stats, IngestStats)
    assert stats.inserted == 1
    refs = list_references(limit=10)
    assert len(refs) == 1
    assert refs[0].sample_id == 0


# --------------------------------------------------------------------------- #
# FAISS reconcile.                                                            #
# --------------------------------------------------------------------------- #


def test_reconcile_with_faiss_assigns_embedding_idx(
    isolated_db: str, markets_parquet: Path, index_meta_json: Path
) -> None:
    from sqlalchemy import select

    asyncio.run(ingest_corpus_markets(markets_parquet))
    updated = asyncio.run(reconcile_with_faiss(index_meta_json))
    assert updated == 3
    with session_scope() as session:
        by_id = {
            r.market_id: r
            for r in session.execute(select(CorpusMarket)).scalars()
        }
        assert by_id["m-1"].embedding_idx == 0
        assert by_id["m-2"].embedding_idx == 1
        assert by_id["m-3"].embedding_idx == 2


# --------------------------------------------------------------------------- #
# CorpusLookup API.                                                           #
# --------------------------------------------------------------------------- #


class _StubFaissIndex:
    """Tiny FAISS-shaped stub that returns deterministic neighbours."""

    def __init__(self, vectors: np.ndarray) -> None:
        self._vectors = vectors
        self.ntotal = len(vectors)

    def search(self, query: np.ndarray, k: int):
        sims = self._vectors @ query[0]
        order = np.argsort(-sims)[:k]
        return np.asarray([sims[order]], dtype="float32"), np.asarray([order], dtype="int64")


class _StubEncoder:
    def __init__(self, vectors_by_text: dict[str, np.ndarray]) -> None:
        self._lookup = vectors_by_text

    def encode(self, texts, **kwargs):  # mimic sentence-transformers
        out = []
        for t in texts:
            vec = self._lookup.get(t, np.zeros(4, dtype="float32"))
            out.append(vec)
        return np.asarray(out, dtype="float32")


def _make_faiss_lookup() -> Lookup:
    records = [
        {"idx": 0, "market_id": "m-1", "question": "Will BTC top $100K by 2026?", "category": "Crypto"},
        {"idx": 1, "market_id": "m-2", "question": "Will the Fed cut rates by July 2026?", "category": "Economy"},
        {"idx": 2, "market_id": "m-3", "question": "Did Espresso FDV top $700M?", "category": "Crypto"},
    ]
    vecs = np.eye(3, dtype="float32")
    # Pad to dim=4 to differ from queries.
    padded = np.zeros((3, 4), dtype="float32")
    padded[:, :3] = vecs
    index = _StubFaissIndex(padded)
    encoder = _StubEncoder(
        {
            "btc bull": np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"),
            "fed": np.array([0.0, 1.0, 0.0, 0.0], dtype="float32"),
            "espresso": np.array([0.0, 0.0, 1.0, 0.0], dtype="float32"),
        }
    )
    return Lookup.from_components(index, records, encoder)


def test_corpus_lookup_find_similar_enriches_from_db(
    isolated_db: str, markets_parquet: Path
) -> None:
    asyncio.run(ingest_corpus_markets(markets_parquet))
    lookup = CorpusLookup.from_faiss(_make_faiss_lookup())
    hits = lookup.find_similar("btc bull", k=2)
    assert hits, "expected at least one hit"
    top = hits[0]
    assert isinstance(top, EnrichedSimilarHit)
    assert top.market_id == "m-1"
    # m-1 was closed -> state=closed; m-3 resolved -> outcome=NO.
    states = {h.market_id: h.state for h in hits}
    assert states["m-1"] == CorpusMarketState.CLOSED.value


def test_corpus_lookup_find_similar_without_enrich(
    isolated_db: str, markets_parquet: Path
) -> None:
    asyncio.run(ingest_corpus_markets(markets_parquet))
    lookup = CorpusLookup.from_faiss(_make_faiss_lookup(), enrich_from_db=False)
    hits = lookup.find_similar("btc bull", k=2)
    assert hits and isinstance(hits[0], SimilarHit)


def test_corpus_lookup_get_few_shots(
    isolated_db: str, few_shots_json: Path
) -> None:
    asyncio.run(ingest_few_shots(few_shots_json, default_dimension="D2"))
    lookup = CorpusLookup.from_faiss(_make_faiss_lookup())
    d2 = lookup.get_few_shots("D2", limit=5)
    assert len(d2) == 1
    assert d2[0].market_id == "561986"
    d5 = lookup.get_few_shots("D5", limit=5)
    assert len(d5) == 1


def test_corpus_lookup_get_style_rules(
    isolated_db: str, style_guide_md: Path
) -> None:
    asyncio.run(ingest_style_guide(style_guide_md))
    lookup = CorpusLookup.from_faiss(_make_faiss_lookup())
    assert len(lookup.get_style_rules()) == 4
    d7 = lookup.get_style_rules(dimension="D7")
    assert len(d7) == 1
    assert "Leading" in d7[0].rule_text


def test_corpus_lookup_get_reference(
    isolated_db: str, reference_dir: Path
) -> None:
    asyncio.run(ingest_reference_translations(reference_dir))
    lookup = CorpusLookup.from_faiss(_make_faiss_lookup())
    ref = lookup.get_reference(0)
    assert ref is not None
    assert ref.primary_translation.startswith("Will the PBOC")
    assert lookup.get_reference(999) is None


def test_corpus_lookup_search_resolved_markets(
    isolated_db: str, markets_parquet: Path
) -> None:
    asyncio.run(ingest_corpus_markets(markets_parquet))
    lookup = CorpusLookup.from_faiss(_make_faiss_lookup())
    resolved = lookup.search_resolved_markets({}, limit=10)
    assert len(resolved) == 1
    assert resolved[0].market_id == "m-3"

    filtered_by_outcome = lookup.search_resolved_markets({"outcome": "YES"}, limit=10)
    assert filtered_by_outcome == []

    closed_only = lookup.search_resolved_markets({"state": "closed"}, limit=10)
    assert {m.market_id for m in closed_only} == {"m-1"}


def test_corpus_lookup_get_pattern_priors(isolated_db: str) -> None:
    with session_scope() as session:
        session.add_all(
            [
                CorpusMarket(
                    market_id="x1",
                    question="Will X by Y?",
                    state=CorpusMarketState.RESOLVED.value,
                    outcome="YES",
                    framing_pattern="P1",
                ),
                CorpusMarket(
                    market_id="x2",
                    question="Will Z by Y?",
                    state=CorpusMarketState.RESOLVED.value,
                    outcome="YES",
                    framing_pattern="P1",
                ),
                CorpusMarket(
                    market_id="x3",
                    question="How many ... by Y?",
                    state=CorpusMarketState.RESOLVED.value,
                    outcome="NO",
                    framing_pattern="P6",
                ),
                CorpusMarket(
                    market_id="x4",
                    question="Random",
                    state=CorpusMarketState.ACTIVE.value,
                    framing_pattern=None,
                ),
            ]
        )
    lookup = CorpusLookup.from_faiss(_make_faiss_lookup())
    priors = lookup.get_pattern_priors()
    # 3 classified rows: 2 P1 + 1 P6 = ratios 2/3 and 1/3.
    assert priors["P1"] == pytest.approx(2 / 3)
    assert priors["P6"] == pytest.approx(1 / 3)
    assert "None" not in priors and None not in priors


# --------------------------------------------------------------------------- #
# Cross-table: BacktestResult -> CorpusMarket FK + JOIN.                       #
# --------------------------------------------------------------------------- #


def test_backtest_result_joins_corpus_market(
    isolated_db: str, markets_parquet: Path
) -> None:
    asyncio.run(ingest_corpus_markets(markets_parquet))
    with session_scope() as session:
        session.add(
            BacktestResult(
                market_id="m-3",
                agent_address="0xagent",
                predicted_outcome="NO",
                actual_outcome="NO",
                correct=True,
                judge_verdict="PASS",
                judge_score=0.91,
            )
        )
    with session_scope() as session:
        from sqlalchemy import select

        stmt = (
            select(BacktestResult, CorpusMarket)
            .join(CorpusMarket, CorpusMarket.market_id == BacktestResult.market_id)
            .where(BacktestResult.agent_address == "0xagent")
        )
        results = list(session.execute(stmt))
        assert len(results) == 1
        backtest, market = results[0]
        assert backtest.correct is True
        assert market.outcome == "NO"
