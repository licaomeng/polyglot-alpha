"""Unified corpus lookup API: file-system FAISS (fast path) + DB queries.

Public surface:

    CorpusLookup()                                       # default: lazy-load FAISS
    .find_similar(query, k=5)        -> list[SimilarHit] # FAISS first, DB enrich
    .get_few_shots(dimension, role)  -> list[FewShotExemplar]
    .get_style_rules(dimension=None) -> list[StyleRule]
    .get_reference(sample_id)        -> ReferenceTranslation | None
    .search_resolved_markets(filters, limit) -> list[CorpusMarket]
    .get_pattern_priors()            -> dict[str, float]

The class is intentionally cheap to construct: FAISS loading is deferred
to the first ``find_similar`` call, and DB queries open a fresh session
each call (relying on the module-level engine in
``polyglot_alpha.persistence.db``).
"""
from __future__ import annotations

import logging
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import func, select
from sqlmodel import Session

from polyglot_alpha.corpus.lookup import (
    DEFAULT_INDEX_PATH,
    DEFAULT_META_PATH,
    DEFAULT_MODEL_NAME,
    Lookup,
    SimilarHit,
)
from polyglot_alpha.persistence import db as persistence_db
from polyglot_alpha.persistence.models import (
    CorpusMarket,
    CorpusMarketState,
    FewShotExemplar,
    FewShotRole,
    ReferenceTranslation,
    StyleRule,
)


@contextmanager
def _read_session() -> Generator[Session, None, None]:
    """Read-only session that keeps attributes alive after detach.

    We use ``expire_on_commit=False`` so that returned ORM rows remain
    usable by callers once the session has been closed.
    """

    session = Session(persistence_db.engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrichedSimilarHit:
    """SimilarHit + DB enrichment fields (state, outcome, framing pattern)."""

    question: str
    score: float
    market_id: str
    category: str = ""
    state: Optional[str] = None
    outcome: Optional[str] = None
    framing_pattern: Optional[str] = None
    total_volume_usdc: Optional[float] = None


class CorpusLookup:
    """Wraps file-FAISS lookup + DB queries behind a single facade."""

    def __init__(
        self,
        *,
        faiss_lookup: Optional[Lookup] = None,
        index_path: Path = DEFAULT_INDEX_PATH,
        meta_path: Path = DEFAULT_META_PATH,
        model_name: str = DEFAULT_MODEL_NAME,
        enrich_from_db: bool = True,
    ) -> None:
        self._faiss = faiss_lookup
        self._index_path = index_path
        self._meta_path = meta_path
        self._model_name = model_name
        self._enrich_from_db = enrich_from_db
        self._faiss_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Construction helpers.                                              #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_faiss(cls, faiss_lookup: Lookup, *, enrich_from_db: bool = True) -> "CorpusLookup":
        """Test-friendly constructor that injects a pre-built Lookup."""

        return cls(faiss_lookup=faiss_lookup, enrich_from_db=enrich_from_db)

    def _get_faiss(self) -> Optional[Lookup]:
        if self._faiss is not None:
            return self._faiss
        with self._faiss_lock:
            if self._faiss is None:
                try:
                    self._faiss = Lookup.load(
                        index_path=self._index_path,
                        meta_path=self._meta_path,
                        model_name=self._model_name,
                    )
                except FileNotFoundError as exc:
                    LOGGER.warning("FAISS index unavailable: %s", exc)
                    return None
        return self._faiss

    # ------------------------------------------------------------------ #
    # Similarity search (FAISS first, DB enrichment second).             #
    # ------------------------------------------------------------------ #

    def find_similar(
        self,
        query: str,
        k: int = 5,
        *,
        enrich: Optional[bool] = None,
    ) -> list[SimilarHit] | list[EnrichedSimilarHit]:
        faiss = self._get_faiss()
        if faiss is None:
            return []
        hits = faiss.find_similar(query, k=k)
        should_enrich = enrich if enrich is not None else self._enrich_from_db
        if not should_enrich or not hits:
            return hits

        market_ids = [h.market_id for h in hits if h.market_id]
        enrichment: dict[str, CorpusMarket] = {}
        if market_ids:
            with _read_session() as session:
                stmt = select(CorpusMarket).where(CorpusMarket.market_id.in_(market_ids))
                for row in session.execute(stmt).scalars():
                    enrichment[row.market_id] = row

        enriched: list[EnrichedSimilarHit] = []
        for hit in hits:
            row = enrichment.get(hit.market_id)
            enriched.append(
                EnrichedSimilarHit(
                    question=hit.question,
                    score=hit.score,
                    market_id=hit.market_id,
                    category=hit.category,
                    state=row.state if row else None,
                    outcome=row.outcome if row else None,
                    framing_pattern=row.framing_pattern if row else None,
                    total_volume_usdc=row.total_volume_usdc if row else None,
                )
            )
        return enriched

    # ------------------------------------------------------------------ #
    # DB-backed lookups.                                                 #
    # ------------------------------------------------------------------ #

    def get_few_shots(
        self,
        dimension: str,
        role: str = FewShotRole.POSITIVE_EXAMPLE.value,
        limit: int = 5,
    ) -> list[FewShotExemplar]:
        """For T4 D1-D7 LLM-judge prompts."""

        with _read_session() as session:
            stmt = (
                select(FewShotExemplar)
                .where(FewShotExemplar.judge_dimension == dimension)
                .where(FewShotExemplar.role == role)
                .order_by(FewShotExemplar.weight.desc(), FewShotExemplar.id.asc())
                .limit(limit)
            )
            rows = list(session.execute(stmt).scalars())
            # Detach so the caller can use rows after the session closes.
            for r in rows:
                session.expunge(r)
            return rows

    def get_style_rules(self, dimension: Optional[str] = None) -> list[StyleRule]:
        """For LLM-judge system prompts."""

        with _read_session() as session:
            stmt = select(StyleRule)
            if dimension is not None:
                stmt = stmt.where(StyleRule.dimension == dimension)
            stmt = stmt.order_by(StyleRule.id.asc())
            rows = list(session.execute(stmt).scalars())
            for r in rows:
                session.expunge(r)
            return rows

    def get_reference(self, sample_id: int) -> Optional[ReferenceTranslation]:
        """For T4 BLEU/COMET judges to score against human reference."""

        with _read_session() as session:
            row = session.get(ReferenceTranslation, sample_id)
            if row is not None:
                session.expunge(row)
            return row

    def search_resolved_markets(
        self,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 100,
    ) -> list[CorpusMarket]:
        """For backtest agent to pull historical events.

        Supported filter keys:
            category, outcome, framing_pattern, state,
            min_volume, max_volume,
            resolved_after, resolved_before
        """

        filters = filters or {}
        with _read_session() as session:
            stmt = select(CorpusMarket)
            # Default: resolved markets only unless caller passes state explicitly.
            state_filter = filters.get("state")
            if state_filter is None:
                stmt = stmt.where(CorpusMarket.state == CorpusMarketState.RESOLVED.value)
            else:
                stmt = stmt.where(CorpusMarket.state == state_filter)

            if (cat := filters.get("category")) is not None:
                stmt = stmt.where(CorpusMarket.category == cat)
            if (outcome := filters.get("outcome")) is not None:
                stmt = stmt.where(CorpusMarket.outcome == outcome)
            if (pattern := filters.get("framing_pattern")) is not None:
                stmt = stmt.where(CorpusMarket.framing_pattern == pattern)
            if (min_vol := filters.get("min_volume")) is not None:
                stmt = stmt.where(CorpusMarket.total_volume_usdc >= float(min_vol))
            if (max_vol := filters.get("max_volume")) is not None:
                stmt = stmt.where(CorpusMarket.total_volume_usdc <= float(max_vol))
            if (after := filters.get("resolved_after")) is not None:
                stmt = stmt.where(CorpusMarket.resolved_at >= after)
            if (before := filters.get("resolved_before")) is not None:
                stmt = stmt.where(CorpusMarket.resolved_at <= before)

            stmt = stmt.order_by(CorpusMarket.resolved_at.desc().nullslast()).limit(limit)
            rows = list(session.execute(stmt).scalars())
            for r in rows:
                session.expunge(r)
            return rows

    def get_pattern_priors(self) -> dict[str, float]:
        """For T4 D1 priors — computed from CorpusMarket.framing_pattern freq."""

        with _read_session() as session:
            stmt = select(
                CorpusMarket.framing_pattern, func.count(CorpusMarket.market_id)
            ).group_by(CorpusMarket.framing_pattern)
            counts: Counter[str] = Counter()
            for pattern, count in session.execute(stmt):
                if pattern is None:
                    continue
                counts[str(pattern)] += int(count)
        total = sum(counts.values())
        if total == 0:
            return {}
        return {pattern: count / total for pattern, count in counts.items()}


# --------------------------------------------------------------------------- #
# Module-level cached singleton (mirrors lookup.find_similar contract).       #
# --------------------------------------------------------------------------- #


_DEFAULT_LOCK = threading.Lock()
_DEFAULT_CORPUS_LOOKUP: Optional[CorpusLookup] = None


def get_default_corpus_lookup() -> CorpusLookup:
    global _DEFAULT_CORPUS_LOOKUP
    if _DEFAULT_CORPUS_LOOKUP is not None:
        return _DEFAULT_CORPUS_LOOKUP
    with _DEFAULT_LOCK:
        if _DEFAULT_CORPUS_LOOKUP is None:
            _DEFAULT_CORPUS_LOOKUP = CorpusLookup()
    return _DEFAULT_CORPUS_LOOKUP


def _reset_default_corpus_lookup_for_tests() -> None:
    global _DEFAULT_CORPUS_LOOKUP
    with _DEFAULT_LOCK:
        _DEFAULT_CORPUS_LOOKUP = None


__all__ = [
    "CorpusLookup",
    "EnrichedSimilarHit",
    "get_default_corpus_lookup",
]
