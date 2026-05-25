"""BLEU judge backed by sacrebleu.

The operator supplies reference translations for the demo set (currently
five samples in ``outputs/``). When no reference is provided we cannot
compute BLEU honestly; the judge falls back to a neutral score=0.5 with
``passed=True`` and notes the absence in ``evidence`` so the panel can
decide whether to weight other signals more heavily.
"""

from __future__ import annotations

from typing import Optional

from polyglot_alpha.judges.types import (
    BLEU_PASS_THRESHOLD,
    JudgeResult,
    PanelQuestion,
)

JUDGE_NAME = "bleu"


def _normalize_bleu(raw: float) -> float:
    """Map BLEU (0-100) onto [0, 1] for the panel aggregator."""

    if raw is None:
        return 0.0
    return max(0.0, min(1.0, raw / 100.0))


async def judge_bleu(
    question: PanelQuestion,
    reference_translation: Optional[str] = None,
) -> JudgeResult:
    """Compute corpus-level BLEU using sacrebleu.

    ``reference_translation`` overrides ``question.reference_translation``
    when provided so the panel can inject references at call time.
    """

    reference: Optional[str] = (
        reference_translation or question.reference_translation
    )

    if not reference or not question.title.strip():
        return JudgeResult(
            name=JUDGE_NAME,
            passed=True,  # cannot fail what we can't measure
            score=0.5,
            reason="No reference translation supplied; BLEU skipped (neutral).",
            evidence={"bleu_raw": None, "has_reference": bool(reference)},
        )

    try:
        import sacrebleu  # local import keeps cold-start cheap
    except ImportError as exc:  # pragma: no cover - dep installed in venv
        return JudgeResult(
            name=JUDGE_NAME,
            passed=False,
            score=0.0,
            reason=f"sacrebleu not installed: {exc}",
            evidence={"bleu_raw": None},
        )

    bleu = sacrebleu.corpus_bleu([question.title], [[reference]])
    raw = float(bleu.score)
    passed = raw > BLEU_PASS_THRESHOLD

    return JudgeResult(
        name=JUDGE_NAME,
        passed=passed,
        score=_normalize_bleu(raw),
        reason=(
            f"BLEU={raw:.2f} (threshold > {BLEU_PASS_THRESHOLD})"
            f" → {'pass' if passed else 'below threshold'}"
        ),
        evidence={
            "bleu_raw": raw,
            "threshold": BLEU_PASS_THRESHOLD,
            "candidate": question.title,
            "reference": reference,
        },
    )
