"""Corpus ingestion pipeline: parquet/JSON/markdown -> SQLModel rows.

All ingest functions are idempotent (upsert-on-conflict semantics) and
batched for memory safety on large parquet files.

CLI:
    python -m polyglot_alpha.corpus.db_ingestion --all
    python -m polyglot_alpha.corpus.db_ingestion --markets corpus/full/polymarket_all_markets.parquet
    python -m polyglot_alpha.corpus.db_ingestion --references outputs/ground_truth/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import pandas as pd
from sqlalchemy import select
from sqlmodel import Session

from polyglot_alpha.persistence import session_scope
from polyglot_alpha.persistence.models import (
    CorpusMarket,
    CorpusMarketState,
    FewShotExemplar,
    FewShotRole,
    ReferenceTranslation,
    StyleRule,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_MARKETS_PARQUET = Path("corpus/polymarket_questions.parquet")
DEFAULT_RESOLVED_PARQUET = Path("corpus/polymarket_resolved.parquet")
DEFAULT_FULL_PARQUET = Path("corpus/full/polymarket_all_markets.parquet")
DEFAULT_FEW_SHOTS_JSON = Path("corpus/few_shots.json")
DEFAULT_STYLE_GUIDE_MD = Path("corpus/style_guide.md")
DEFAULT_INDEX_META = Path("corpus/index_meta.json")
DEFAULT_REFERENCES_DIR = Path("outputs/ground_truth")

_DEFAULT_BATCH_SIZE = 1000


# --------------------------------------------------------------------------- #
# Coercion helpers.                                                           #
# --------------------------------------------------------------------------- #


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        ts = pd.to_datetime(value, utc=True, errors="coerce")
    except Exception:  # noqa: BLE001
        return None
    if ts is None or pd.isna(ts):
        return None
    return ts.to_pydatetime().replace(tzinfo=None)


def _coerce_list(value: Any) -> Optional[list[Any]]:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if hasattr(value, "tolist"):
        try:
            return list(value.tolist())
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return default


def _infer_state(row: dict[str, Any]) -> str:
    if row.get("uma_dispute"):
        return CorpusMarketState.DISPUTED.value
    if row.get("resolved_at") or row.get("outcome"):
        return CorpusMarketState.RESOLVED.value
    if _coerce_bool(row.get("closed")):
        return CorpusMarketState.CLOSED.value
    return CorpusMarketState.ACTIVE.value


def _row_to_corpus_market_kwargs(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    market_id = row.get("market_id")
    if market_id is None or (isinstance(market_id, float) and pd.isna(market_id)):
        return None
    question = row.get("question") or ""
    if not str(question).strip():
        return None
    outcome = row.get("outcome") or row.get("winning_outcome")
    if isinstance(outcome, str):
        outcome = outcome.strip().upper() or None
    return {
        "market_id": str(market_id),
        "question": str(question),
        "category": row.get("category") or None,
        "subcategory": row.get("subcategory"),
        "tags": _coerce_list(row.get("tags")),
        "created_at": _coerce_datetime(row.get("created_at")),
        "end_date": _coerce_datetime(row.get("end_date") or row.get("resolution_date")),
        "resolved_at": _coerce_datetime(row.get("resolved_at")),
        "state": _infer_state(row),
        "outcome": outcome,
        "outcome_prices": _coerce_list(row.get("outcome_prices")),
        "total_volume_usdc": _coerce_float(
            row.get("total_volume_usdc") or row.get("volume_usd")
        ),
        "uma_dispute": _coerce_bool(row.get("uma_dispute")),
        "resolution_source": row.get("resolution_source") or None,
        "is_community_created": _coerce_bool(row.get("is_community_created")),
        "embedding_idx": row.get("embedding_idx"),
        "framing_pattern": row.get("framing_pattern"),
    }


def _iter_parquet_batches(
    path: Path, batch_size: int
) -> Iterator[list[dict[str, Any]]]:
    """Yield row dict batches from a parquet file."""

    df = pd.read_parquet(path)
    LOGGER.info("Loaded %s rows from %s", len(df), path)
    for start in range(0, len(df), batch_size):
        chunk = df.iloc[start : start + batch_size]
        yield chunk.to_dict(orient="records")


# --------------------------------------------------------------------------- #
# Upsert primitives.                                                          #
# --------------------------------------------------------------------------- #


def _upsert_corpus_market(session: Session, kwargs: dict[str, Any]) -> bool:
    """Insert-or-update a single corpus_markets row. Returns True if new."""

    market_id = kwargs["market_id"]
    existing = session.get(CorpusMarket, market_id)
    if existing is None:
        session.add(CorpusMarket(**kwargs))
        return True
    for key, value in kwargs.items():
        if key == "market_id":
            continue
        # Preserve non-null DB values when incoming is None (so partial
        # updates don't wipe enrichment from the resolved-corpus pass).
        if value is None and getattr(existing, key) is not None:
            continue
        setattr(existing, key, value)
    return False


# --------------------------------------------------------------------------- #
# Public ingestion API (async wrappers around sync DB writes).                #
# --------------------------------------------------------------------------- #


@dataclass
class IngestStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped


async def ingest_corpus_markets(
    parquet_path: Path = DEFAULT_MARKETS_PARQUET,
    *,
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> IngestStats:
    """Stream parquet -> CorpusMarket rows, idempotent (upsert by market_id)."""

    if not parquet_path.exists():
        raise FileNotFoundError(parquet_path)

    return await asyncio.to_thread(_sync_ingest_corpus_markets, parquet_path, batch_size)


def _sync_ingest_corpus_markets(parquet_path: Path, batch_size: int) -> IngestStats:
    stats = IngestStats()
    for batch in _iter_parquet_batches(parquet_path, batch_size):
        with session_scope() as session:
            for row in batch:
                kwargs = _row_to_corpus_market_kwargs(row)
                if kwargs is None:
                    stats.skipped += 1
                    continue
                if _upsert_corpus_market(session, kwargs):
                    stats.inserted += 1
                else:
                    stats.updated += 1
    LOGGER.info(
        "Ingested corpus markets: %d inserted / %d updated / %d skipped",
        stats.inserted,
        stats.updated,
        stats.skipped,
    )
    return stats


async def ingest_few_shots(
    json_path: Path = DEFAULT_FEW_SHOTS_JSON,
    *,
    default_dimension: str = "D2",
) -> IngestStats:
    """Load corpus/few_shots.json -> FewShotExemplar rows."""

    if not json_path.exists():
        raise FileNotFoundError(json_path)

    return await asyncio.to_thread(
        _sync_ingest_few_shots, json_path, default_dimension
    )


def _sync_ingest_few_shots(json_path: Path, default_dimension: str) -> IngestStats:
    data = json.loads(json_path.read_text())
    examples = data.get("examples", data) if isinstance(data, dict) else data
    if not isinstance(examples, list):
        raise ValueError(f"Unexpected few-shots payload shape in {json_path}")

    stats = IngestStats()
    with session_scope() as session:
        for ex in examples:
            if not isinstance(ex, dict):
                stats.skipped += 1
                continue
            market_id = ex.get("market_id")
            market_id = str(market_id) if market_id is not None else None
            question_text = ex.get("title") or ex.get("question") or ""
            if not question_text:
                stats.skipped += 1
                continue
            explanation = ex.get("why_good_exemplar") or ex.get("explanation") or ""
            dimension = ex.get("judge_dimension") or default_dimension
            role = ex.get("role") or FewShotRole.POSITIVE_EXAMPLE.value
            weight = float(ex.get("weight", 1.0))

            # Idempotency: skip if (market_id, judge_dimension, role) already exists.
            stmt = select(FewShotExemplar).where(
                FewShotExemplar.market_id == market_id,
                FewShotExemplar.judge_dimension == dimension,
                FewShotExemplar.role == role,
                FewShotExemplar.question_text == question_text,
            )
            if session.execute(stmt).first():
                stats.updated += 1
                continue

            session.add(
                FewShotExemplar(
                    market_id=market_id,
                    judge_dimension=dimension,
                    role=role,
                    question_text=question_text,
                    explanation=explanation,
                    weight=weight,
                )
            )
            stats.inserted += 1
    LOGGER.info(
        "Ingested few-shots: %d inserted / %d already-present / %d skipped",
        stats.inserted,
        stats.updated,
        stats.skipped,
    )
    return stats


_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_DIMENSION_RE = re.compile(r"\b(D[1-8])\b")
_STYLE_DIMENSION_HINTS = {
    "structure": "D2",
    "tone": "D2",
    "resolution": "D5",
    "granularity": "D4",
    "leading": "D7",
}


async def ingest_style_guide(
    md_path: Path = DEFAULT_STYLE_GUIDE_MD,
) -> IngestStats:
    """Parse corpus/style_guide.md bullets -> StyleRule rows."""

    if not md_path.exists():
        raise FileNotFoundError(md_path)

    return await asyncio.to_thread(_sync_ingest_style_guide, md_path)


def _sync_ingest_style_guide(md_path: Path) -> IngestStats:
    stats = IngestStats()
    text = md_path.read_text()
    with session_scope() as session:
        for line in text.splitlines():
            m = _BULLET_RE.match(line)
            if not m:
                continue
            rule_text = m.group(1).strip()
            if not rule_text:
                continue

            dim_match = _DIMENSION_RE.search(rule_text)
            dimension: Optional[str] = dim_match.group(1) if dim_match else None
            if dimension is None:
                low = rule_text.lower()
                for hint, dim in _STYLE_DIMENSION_HINTS.items():
                    if hint in low:
                        dimension = dim
                        break

            # Idempotency: skip duplicates by full text.
            stmt = select(StyleRule).where(StyleRule.rule_text == rule_text)
            if session.execute(stmt).first():
                stats.updated += 1
                continue

            session.add(
                StyleRule(
                    rule_text=rule_text,
                    dimension=dimension,
                    source="llm_distilled",
                    confidence=1.0,
                )
            )
            stats.inserted += 1
    LOGGER.info(
        "Ingested style rules: %d inserted / %d already-present", stats.inserted, stats.updated
    )
    return stats


async def ingest_reference_translations(
    path: Path = DEFAULT_REFERENCES_DIR,
) -> IngestStats:
    """Load outputs/ground_truth/*.json -> ReferenceTranslation rows.

    Accepts either a directory of per-sample JSON files or a single
    JSONL file.
    """

    if not path.exists():
        raise FileNotFoundError(path)

    return await asyncio.to_thread(_sync_ingest_reference_translations, path)


def _sync_ingest_reference_translations(path: Path) -> IngestStats:
    if path.is_dir():
        json_files = sorted(path.glob("*.json"))
    elif path.suffix in {".jsonl", ".ndjson"}:
        json_files = [path]
    else:
        json_files = [path]

    stats = IngestStats()
    with session_scope() as session:
        for fp in json_files:
            if fp.suffix in {".jsonl", ".ndjson"}:
                records = [json.loads(line) for line in fp.read_text().splitlines() if line.strip()]
            else:
                records = [json.loads(fp.read_text())]
            for record in records:
                kwargs = _reference_record_to_kwargs(record)
                if kwargs is None:
                    stats.skipped += 1
                    continue
                sample_id = kwargs["sample_id"]
                existing = session.get(ReferenceTranslation, sample_id)
                if existing is None:
                    session.add(ReferenceTranslation(**kwargs))
                    stats.inserted += 1
                else:
                    for key, value in kwargs.items():
                        setattr(existing, key, value)
                    stats.updated += 1
    LOGGER.info(
        "Ingested reference translations: %d inserted / %d updated / %d skipped",
        stats.inserted,
        stats.updated,
        stats.skipped,
    )
    return stats


def _reference_record_to_kwargs(record: dict[str, Any]) -> Optional[dict[str, Any]]:
    sample_id = record.get("sample_id")
    if sample_id is None:
        return None
    gt = record.get("ground_truth_translation") or {}
    primary = gt.get("primary") if isinstance(gt, dict) else None
    if not primary:
        # Fall back to top-level keys for flat schemas.
        primary = record.get("primary_translation") or record.get("primary") or ""
    if not primary:
        return None
    alt_phrasings = (
        gt.get("alternative_phrasings")
        if isinstance(gt, dict)
        else None
    ) or record.get("alternative_phrasings") or []
    k5_variants = record.get("k5_framing_variants") or []
    return {
        "sample_id": int(sample_id),
        "source_chinese": record.get("source_chinese", ""),
        "primary_translation": primary,
        "alternative_phrasings": list(alt_phrasings),
        "k5_framing_variants": list(k5_variants),
        "expected_bleu_threshold": float(record.get("expected_bleu_threshold", 25.0)),
        "expected_comet_threshold": float(record.get("expected_comet_threshold", 0.55)),
        "polymarket_shape_validation": record.get("polymarket_shape_validation"),
        "annotator_notes": record.get("annotator_notes"),
    }


async def reconcile_with_faiss(
    meta_path: Path = DEFAULT_INDEX_META,
) -> int:
    """Link CorpusMarket.embedding_idx to FAISS index positions via market_id.

    Returns the number of rows updated.
    """

    if not meta_path.exists():
        raise FileNotFoundError(meta_path)

    return await asyncio.to_thread(_sync_reconcile_with_faiss, meta_path)


def _sync_reconcile_with_faiss(meta_path: Path) -> int:
    payload = json.loads(meta_path.read_text())
    records = payload.get("records", [])
    updated = 0
    with session_scope() as session:
        for rec in records:
            market_id = rec.get("market_id")
            idx = rec.get("idx")
            if market_id is None or idx is None:
                continue
            row = session.get(CorpusMarket, str(market_id))
            if row is None:
                continue
            if row.embedding_idx != idx:
                row.embedding_idx = int(idx)
                updated += 1
    LOGGER.info("Reconciled embedding_idx for %d corpus_markets rows", updated)
    return updated


# --------------------------------------------------------------------------- #
# CLI entry point.                                                            #
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest PolyglotAlpha corpus files into the DB."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Ingest every default source (markets, few-shots, style, references, FAISS reconcile).",
    )
    parser.add_argument(
        "--markets",
        type=Path,
        nargs="?",
        const=DEFAULT_MARKETS_PARQUET,
        help="Parquet file with corpus markets.",
    )
    parser.add_argument(
        "--resolved",
        type=Path,
        nargs="?",
        const=DEFAULT_RESOLVED_PARQUET,
        help="Parquet file with resolved markets.",
    )
    parser.add_argument(
        "--few-shots",
        type=Path,
        nargs="?",
        const=DEFAULT_FEW_SHOTS_JSON,
        help="Few-shots JSON file.",
    )
    parser.add_argument(
        "--style-guide",
        type=Path,
        nargs="?",
        const=DEFAULT_STYLE_GUIDE_MD,
        help="Style-guide markdown file.",
    )
    parser.add_argument(
        "--references",
        type=Path,
        nargs="?",
        const=DEFAULT_REFERENCES_DIR,
        help="Directory or JSONL of reference translations.",
    )
    parser.add_argument(
        "--reconcile-faiss",
        action="store_true",
        help="Reconcile embedding_idx from FAISS index metadata.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=_DEFAULT_BATCH_SIZE
    )
    return parser


async def _run_cli(args: argparse.Namespace) -> int:
    from polyglot_alpha.persistence import init_db

    init_db()

    did_anything = False

    if args.all or args.markets:
        markets_path = args.markets if args.markets else DEFAULT_MARKETS_PARQUET
        if markets_path.exists():
            await ingest_corpus_markets(markets_path, batch_size=args.batch_size)
            did_anything = True
        else:
            LOGGER.warning("Markets parquet not found: %s", markets_path)

    if args.all or args.resolved:
        resolved_path = args.resolved if args.resolved else DEFAULT_RESOLVED_PARQUET
        if resolved_path.exists():
            await ingest_corpus_markets(resolved_path, batch_size=args.batch_size)
            did_anything = True
        else:
            LOGGER.warning("Resolved parquet not found: %s", resolved_path)

    if args.all or args.few_shots:
        fs_path = args.few_shots if args.few_shots else DEFAULT_FEW_SHOTS_JSON
        if fs_path.exists():
            await ingest_few_shots(fs_path)
            did_anything = True
        else:
            LOGGER.warning("Few-shots JSON not found: %s", fs_path)

    if args.all or args.style_guide:
        sg_path = args.style_guide if args.style_guide else DEFAULT_STYLE_GUIDE_MD
        if sg_path.exists():
            await ingest_style_guide(sg_path)
            did_anything = True
        else:
            LOGGER.warning("Style guide not found: %s", sg_path)

    if args.all or args.references:
        ref_path = args.references if args.references else DEFAULT_REFERENCES_DIR
        if ref_path.exists():
            await ingest_reference_translations(ref_path)
            did_anything = True
        else:
            LOGGER.warning("References not found: %s", ref_path)

    if args.all or args.reconcile_faiss:
        meta_path = DEFAULT_INDEX_META
        if meta_path.exists():
            await reconcile_with_faiss(meta_path)
            did_anything = True
        else:
            LOGGER.warning("FAISS meta not found: %s", meta_path)

    if not did_anything:
        LOGGER.warning("No ingestion source selected. Use --all or a flag.")
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    return asyncio.run(_run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
