"""In-memory FewShotExemplar seeding helper.

The shipped ``polyglot_alpha.corpus.db_ingestion._sync_ingest_few_shots``
reads from a JSON file on disk, which makes it awkward to ingest the
in-memory ``EXTENDED_EXEMPLARS`` list defined in
``polyglot_alpha.corpus.few_shots_extended``. This helper bridges the two
by mapping the ``{dim, role, text, rationale}`` shape used by
EXTENDED_EXEMPLARS to the ``FewShotExemplar`` ORM rows expected by the DB,
applying the same idempotency rule (skip if
``(market_id, judge_dimension, role, question_text)`` already exists).

Used by:
  * ``polyglot_alpha.api.main._maybe_auto_ingest_few_shots`` — runs at
    backend startup when the table is empty.
  * ``scripts/ingest_few_shots.py`` — one-shot CLI invocation for ops.
"""
from __future__ import annotations

import logging
from typing import Iterable, Mapping

from sqlmodel import select

from .persistence import session_scope
from .persistence.models import FewShotExemplar, FewShotRole

logger = logging.getLogger(__name__)


_REQUIRED_KEYS: frozenset[str] = frozenset({"dim", "role", "text"})


def seed_few_shots_from_extended(
    exemplars: Iterable[Mapping[str, str]],
    *,
    default_weight: float = 1.0,
) -> int:
    """Insert ``exemplars`` into ``few_shot_exemplars``, returning new-row count.

    Idempotent: rows whose ``(judge_dimension, role, question_text)``
    already exist are skipped. Rows missing the required ``dim`` / ``role``
    / ``text`` keys are skipped with a warning.
    """

    valid_roles = {role.value for role in FewShotRole}
    inserted = 0
    skipped = 0
    with session_scope() as session:
        for raw in exemplars:
            if not isinstance(raw, Mapping):
                skipped += 1
                continue
            missing = _REQUIRED_KEYS - raw.keys()
            if missing:
                logger.debug(
                    "skipping exemplar (missing keys=%s): %r", sorted(missing), raw
                )
                skipped += 1
                continue
            dimension = str(raw["dim"]).strip()
            role = str(raw["role"]).strip()
            question_text = str(raw["text"]).strip()
            if not (dimension and role and question_text):
                skipped += 1
                continue
            if role not in valid_roles:
                logger.warning(
                    "skipping exemplar with unknown role=%r (dimension=%s)",
                    role,
                    dimension,
                )
                skipped += 1
                continue
            explanation = str(raw.get("rationale") or raw.get("explanation") or "")

            # Idempotency: skip if an identical row is already present.
            stmt = select(FewShotExemplar).where(
                FewShotExemplar.judge_dimension == dimension,
                FewShotExemplar.role == role,
                FewShotExemplar.question_text == question_text,
            )
            if session.exec(stmt).first() is not None:
                continue

            session.add(
                FewShotExemplar(
                    market_id=None,
                    judge_dimension=dimension,
                    role=role,
                    question_text=question_text,
                    explanation=explanation,
                    weight=default_weight,
                )
            )
            inserted += 1
    if skipped:
        logger.info(
            "seed_few_shots_from_extended: inserted=%d skipped=%d", inserted, skipped
        )
    else:
        logger.info("seed_few_shots_from_extended: inserted=%d", inserted)
    return inserted


__all__ = ["seed_few_shots_from_extended"]
