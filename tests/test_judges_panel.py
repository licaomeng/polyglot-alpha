"""Unit tests for the 11-judge quality panel.

We mock the LLM backend so tests never hit Gemini, and bypass the
COMET model by relying on its graceful-degradation path (no checkpoint
on disk → neutral pass).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from polyglot_alpha.judges import PanelQuestion, panel
from polyglot_alpha.judges.style_alignment import (
    judge_d1_structural,
    judge_d2_stylistic,
    judge_d3_framing,
    judge_d4_granularity,
    judge_d5_resolution_clarity,
    judge_d6_source_reliability,
    judge_d7_leading_check,
    judge_d8_duplicate_detection,
)
from polyglot_alpha.judges.style_alignment.llm_batch import clear_cache
from polyglot_alpha.judges.translation import judge_bleu, judge_comet, judge_mqm_llm


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clear_llm_cache() -> None:
    clear_cache()


@pytest.fixture()
def good_question() -> PanelQuestion:
    return PanelQuestion(
        title=(
            "Will the People's Bank of China (PBOC) announce a cut to the "
            "Reserve Requirement Ratio (RRR) before August 23, 2026?"
        ),
        description=(
            "PBOC Governor Pan Gongsheng signaled an imminent RRR cut to "
            "support the real economy."
        ),
        resolution_criteria=(
            "YES if PBOC officially announces a reduction to the Reserve "
            "Requirement Ratio on its official website (pbc.gov.cn) before "
            "August 23, 2026. NO otherwise."
        ),
        resolution_source="http://www.pbc.gov.cn/",
        cutoff_ts="2026-08-23T23:59:59+08:00",
        category="policy/china",
        source_news="央行行长潘功胜表示，将根据需要适时降准。",
        reference_translation=(
            "Will the People's Bank of China announce a cut to the Reserve "
            "Requirement Ratio before August 23, 2026?"
        ),
    )


@pytest.fixture()
def bad_question() -> PanelQuestion:
    return PanelQuestion(
        title="China will obviously announce a stunning rate cut and a VAT extension and stimulus?",
        description="",
        resolution_criteria="",
        resolution_source="https://random-blog.example.com/post",
        cutoff_ts="not-a-date",
        category="policy/china",
        source_news="财政部宣布将延长针对小微企业的增值税减免政策至2027年12月底。",
    )


def _stub_style_llm(payload: dict[str, Any]):
    """Build an async callable that returns ``payload`` serialized as JSON."""

    async def _fn(prompt: str) -> str:
        return json.dumps(payload)

    return _fn


_GOOD_STYLE_PAYLOAD = {
    "d2": {"passed": True, "score": 0.9, "reason": "Neutral, source cited."},
    "d3": {"passed": True, "score": 0.9, "reason": "Predictive framing."},
    "d6": {"passed": True, "score": 0.95, "reason": "Authoritative PBOC URL."},
    "d7": {"passed": True, "score": 0.95, "reason": "No leading bias."},
}

_BAD_STYLE_PAYLOAD = {
    "d2": {"passed": False, "score": 0.2, "reason": "Editorial tone."},
    "d3": {"passed": False, "score": 0.3, "reason": "Declarative, not predictive."},
    "d6": {"passed": False, "score": 0.2, "reason": "Random blog source."},
    "d7": {"passed": False, "score": 0.1, "reason": "Leading language present."},
}


_GOOD_MQM_PAYLOAD = json.dumps(
    {"errors": [], "rationale": "Faithful translation."}
)
_BAD_MQM_PAYLOAD = json.dumps(
    {
        "errors": [
            {"category": "Accuracy", "severity": "MAJOR", "detail": "Adds fact not in source."}
        ],
        "rationale": "Major accuracy error.",
    }
)


async def _stub_mqm(payload: str):
    async def _fn(prompt: str) -> str:
        return payload

    return _fn


# --------------------------------------------------------------------------- #
# Per-judge tests                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_bleu_judge_passes_with_close_reference(good_question: PanelQuestion) -> None:
    result = await judge_bleu(good_question)
    assert result.evidence["bleu_raw"] is not None
    # Reference is identical except for a parenthetical — BLEU should be high.
    assert result.evidence["bleu_raw"] > 25.0
    assert result.passed is True


@pytest.mark.asyncio
async def test_bleu_judge_neutral_without_reference() -> None:
    q = PanelQuestion(title="Will X happen by 2026?")
    result = await judge_bleu(q)
    assert result.passed is True
    assert result.evidence["has_reference"] is False
    assert result.score == 0.5


@pytest.mark.asyncio
async def test_comet_judge_graceful_when_offline(good_question: PanelQuestion) -> None:
    # comet package is not installed in this venv — judge should degrade gracefully.
    result = await judge_comet(good_question)
    assert result.passed is True
    assert result.evidence["comet_raw"] is None or isinstance(
        result.evidence["comet_raw"], float
    )


@pytest.mark.asyncio
async def test_mqm_llm_judge_with_mocked_backend(good_question: PanelQuestion) -> None:
    async def backend(prompt: str) -> str:
        return _GOOD_MQM_PAYLOAD

    result = await judge_mqm_llm(good_question, llm_call=backend)
    assert result.passed is True
    assert result.evidence["major_count"] == 0


@pytest.mark.asyncio
async def test_mqm_llm_judge_fails_on_major_error(good_question: PanelQuestion) -> None:
    async def backend(prompt: str) -> str:
        return _BAD_MQM_PAYLOAD

    result = await judge_mqm_llm(good_question, llm_call=backend)
    assert result.passed is False
    assert result.evidence["major_count"] == 1


@pytest.mark.asyncio
async def test_d1_structural_matches_will_by_date(good_question: PanelQuestion) -> None:
    result = await judge_d1_structural(good_question)
    assert result.passed is True
    assert result.evidence["matched_pattern"] in {"will_x_by_date", "will_x_no_date"}


@pytest.mark.asyncio
async def test_d1_structural_rejects_non_question() -> None:
    q = PanelQuestion(title="The Fed raised rates yesterday.")
    result = await judge_d1_structural(q)
    assert result.passed is False


@pytest.mark.asyncio
async def test_d2_stylistic_uses_batch(good_question: PanelQuestion) -> None:
    backend = _stub_style_llm(_GOOD_STYLE_PAYLOAD)
    result = await judge_d2_stylistic(good_question, llm_call=backend)
    assert result.passed is True


@pytest.mark.asyncio
async def test_d3_framing_uses_batch(good_question: PanelQuestion) -> None:
    backend = _stub_style_llm(_GOOD_STYLE_PAYLOAD)
    result = await judge_d3_framing(good_question, llm_call=backend)
    assert result.passed is True


@pytest.mark.asyncio
async def test_d4_granularity_rejects_compound(bad_question: PanelQuestion) -> None:
    result = await judge_d4_granularity(bad_question)
    assert result.passed is False
    assert "compound" in result.reason.lower() or "split" in result.reason.lower()


@pytest.mark.asyncio
async def test_d4_granularity_accepts_single(good_question: PanelQuestion) -> None:
    result = await judge_d4_granularity(good_question)
    assert result.passed is True


@pytest.mark.asyncio
async def test_d5_resolution_clarity_passes(good_question: PanelQuestion) -> None:
    result = await judge_d5_resolution_clarity(good_question)
    assert result.passed is True


@pytest.mark.asyncio
async def test_d5_resolution_clarity_fails_empty_criteria(bad_question: PanelQuestion) -> None:
    result = await judge_d5_resolution_clarity(bad_question)
    assert result.passed is False


@pytest.mark.asyncio
async def test_d6_source_reliability_authoritative_host(good_question: PanelQuestion) -> None:
    # Even with an LLM "fail" payload, authoritative .gov.cn URL should pass.
    backend = _stub_style_llm(
        {**_GOOD_STYLE_PAYLOAD, "d6": {"passed": False, "score": 0.0, "reason": "n/a"}}
    )
    result = await judge_d6_source_reliability(good_question, llm_call=backend)
    assert result.passed is True
    assert result.evidence["authoritative_host"] is True


@pytest.mark.asyncio
async def test_d7_leading_check_catches_obviously() -> None:
    q = PanelQuestion(
        title="Will the Fed obviously cut rates by 2026?",
        resolution_criteria="YES/NO",
        cutoff_ts="2026-01-01T00:00:00+00:00",
    )
    backend = _stub_style_llm(_GOOD_STYLE_PAYLOAD)
    result = await judge_d7_leading_check(q, llm_call=backend)
    assert result.passed is False
    assert any("obviously" in s.lower() for s in result.evidence["leading_hits"])


@pytest.mark.asyncio
async def test_d8_duplicate_detection_no_corpus(good_question: PanelQuestion) -> None:
    # Default corpus path does not exist — judge should pass with note.
    result = await judge_d8_duplicate_detection(
        good_question, index_path=Path("corpus/does_not_exist.faiss")
    )
    assert result.passed is True
    assert "corpus" in result.reason.lower() or "model" in result.reason.lower()


@pytest.mark.asyncio
async def test_d8_duplicate_detection_with_stub_index(good_question: PanelQuestion) -> None:
    """Stub embed model + FAISS index to force a duplicate hit."""

    class _StubModel:
        def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
            import numpy as np

            return np.ones((len(texts), 4), dtype="float32")

    class _StubIndex:
        def search(self, vec, k):
            import numpy as np

            # Return cosine = 0.99 -> duplicate
            return np.array([[0.99]], dtype="float32"), np.array([[42]])

    result = await judge_d8_duplicate_detection(
        good_question,
        embed_override=_StubModel(),
        index_override=_StubIndex(),
        threshold=0.85,
    )
    assert result.passed is False
    assert result.evidence["max_similarity"] == pytest.approx(0.99, abs=1e-3)


# --------------------------------------------------------------------------- #
# Panel aggregator                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_panel_evaluate_good_question(good_question: PanelQuestion) -> None:
    async def style_backend(prompt: str) -> str:
        return json.dumps(_GOOD_STYLE_PAYLOAD)

    async def mqm_backend(prompt: str) -> str:
        return _GOOD_MQM_PAYLOAD

    verdict = await panel.evaluate(
        good_question,
        llm_call=style_backend,
        mqm_llm_call=mqm_backend,
    )
    assert verdict.overall_pass is True
    assert verdict.verdict == "PASS"
    assert verdict.overall_score > 50
    assert verdict.style_alignment_passes["d1"] is True
    assert verdict.style_alignment_passes["d4"] is True
    assert verdict.style_alignment_passes["d5"] is True
    assert verdict.style_alignment_passes["d8"] is True


@pytest.mark.asyncio
async def test_panel_evaluate_bad_question(bad_question: PanelQuestion) -> None:
    async def style_backend(prompt: str) -> str:
        return json.dumps(_BAD_STYLE_PAYLOAD)

    async def mqm_backend(prompt: str) -> str:
        return _BAD_MQM_PAYLOAD

    verdict = await panel.evaluate(
        bad_question,
        llm_call=style_backend,
        mqm_llm_call=mqm_backend,
    )
    assert verdict.overall_pass is False
    assert verdict.verdict in {"FAIL", "BORDERLINE"}


@pytest.mark.asyncio
async def test_panel_accepts_dict_payload() -> None:
    payload = {
        "title": "Will the CSRC issue supplementary rules by 2026-08-31?",
        "resolution_criteria": "YES if issued before cutoff. NO otherwise.",
        "resolution_source": "http://www.csrc.gov.cn/",
        "cutoff_ts": "2026-08-31T23:59:59+08:00",
        "category": "policy/china",
        "source_news": "证监会修订发布上市公司监管指引第10号。",
    }

    async def style_backend(prompt: str) -> str:
        return json.dumps(_GOOD_STYLE_PAYLOAD)

    async def mqm_backend(prompt: str) -> str:
        return _GOOD_MQM_PAYLOAD

    verdict = await panel.evaluate(
        payload, llm_call=style_backend, mqm_llm_call=mqm_backend
    )
    assert isinstance(verdict.overall_score, int)
    assert 0 <= verdict.overall_score <= 100


# --------------------------------------------------------------------------- #
# Closed-IP weight guard                                                      #
# --------------------------------------------------------------------------- #


def test_weights_blocked_without_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLYGLOT_DEMO_MODE", raising=False)
    # Force the in-process toggle off too.
    from polyglot_alpha.judges import panel as panel_mod

    panel_mod._ALLOW_WEIGHT_ACCESS["value"] = False
    with pytest.raises(RuntimeError) as exc_info:
        _ = panel_mod._weights
    assert "closed IP" in str(exc_info.value)


def test_weights_visible_in_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYGLOT_DEMO_MODE", "1")
    from polyglot_alpha.judges import panel as panel_mod

    weights = panel_mod._weights
    assert isinstance(weights, dict)
    assert weights["mqm_llm"] > 0
    # Sanity: weights should roughly sum to 1.0.
    assert abs(sum(weights.values()) - 1.0) < 0.02


# --------------------------------------------------------------------------- #
# Corrections from README §5.22 (T4 mechanism-design pass)                    #
# --------------------------------------------------------------------------- #


def test_weights_sum_to_exactly_one() -> None:
    """New weight distribution (D5 doubled to 0.12) must sum to 1.00."""

    import os

    os.environ["POLYGLOT_DEMO_MODE"] = "1"
    try:
        from polyglot_alpha.judges import panel as panel_mod

        weights = panel_mod._weights
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        # D5 is the highest-EV style dimension — verify the bump landed.
        assert weights["d5_resolution_clarity"] == pytest.approx(0.12, abs=1e-9)
    finally:
        os.environ.pop("POLYGLOT_DEMO_MODE", None)


def test_weight_access_audit_log_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading ``panel._weights`` in demo mode writes a JSONL audit line."""

    from polyglot_alpha.judges import panel as panel_mod

    log_path = tmp_path / "weight_access_log.jsonl"
    monkeypatch.setattr(panel_mod, "WEIGHT_ACCESS_LOG_PATH", log_path)
    monkeypatch.setenv("POLYGLOT_DEMO_MODE", "1")

    _ = panel_mod._weights

    assert log_path.exists(), "audit log file must be created"
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["reason"] == "demo_mode_read"
    assert "timestamp" in record
    assert "caller" in record


def test_d1_pattern_priors_match_corpus_distribution() -> None:
    """D1 must expose corpus-derived priors with P1 dominance (~85.6%)."""

    from polyglot_alpha.judges.style_alignment.d1_structural import PATTERN_PRIORS

    assert PATTERN_PRIORS["P1_will_by_date"] == pytest.approx(0.856, abs=1e-3)
    assert PATTERN_PRIORS["P1_will_by_date"] > PATTERN_PRIORS["P3_threshold"]
    assert PATTERN_PRIORS["P1_will_by_date"] > PATTERN_PRIORS["P2_noun_phrase"]
    # Sum should be close to 1.0 (allow small residual for unclassified).
    assert abs(sum(PATTERN_PRIORS.values()) - 1.0) < 0.05


@pytest.mark.asyncio
async def test_d8_real_corpus_detects_duplicate() -> None:
    """D8 must flag a literal corpus title as duplicate against the shipped FAISS index."""

    # This title is in corpus/index_meta.json at idx=1.
    q = PanelQuestion(
        title="MicroStrategy sells any Bitcoin by December 31, 2026?"
    )
    result = await judge_d8_duplicate_detection(q)
    assert result.passed is False
    assert result.evidence["max_similarity"] >= 0.92
    assert result.evidence.get("neighbor_question") == q.title


@pytest.mark.asyncio
async def test_d8_real_corpus_accepts_novel_title() -> None:
    """A non-corpus title must pass D8 against the real shipped index."""

    q = PanelQuestion(
        title=(
            "Will the People's Bank of China announce a Reserve Requirement"
            " Ratio cut before August 23, 2026?"
        )
    )
    result = await judge_d8_duplicate_detection(q)
    assert result.passed is True
    # Should be well below the 0.92 duplicate threshold.
    assert result.evidence["max_similarity"] < 0.92


@pytest.mark.asyncio
async def test_panel_hard_gate_d5_failure_fails(good_question: PanelQuestion) -> None:
    """D5 is a hard gate — if it fails, the panel must FAIL even with everything else passing."""

    bad_q = PanelQuestion(
        title=good_question.title,
        description=good_question.description,
        resolution_criteria="",  # empty -> D5 fails
        resolution_source=good_question.resolution_source,
        cutoff_ts="not-a-date",  # also unparseable -> D5 fails harder
        category=good_question.category,
        source_news=good_question.source_news,
        reference_translation=good_question.reference_translation,
    )

    async def style_backend(prompt: str) -> str:
        return json.dumps(_GOOD_STYLE_PAYLOAD)

    async def mqm_backend(prompt: str) -> str:
        return _GOOD_MQM_PAYLOAD

    verdict = await panel.evaluate(
        bad_q, llm_call=style_backend, mqm_llm_call=mqm_backend
    )
    assert verdict.style_alignment_passes["d5"] is False
    assert verdict.overall_pass is False
    assert verdict.verdict in {"FAIL", "BORDERLINE"}


@pytest.mark.asyncio
async def test_panel_soft_gate_threshold_four_of_five(
    good_question: PanelQuestion,
) -> None:
    """Soft gates require >=4 of 5 (D2/D3/D4/D6/D7) — three passing is not enough."""

    # Two soft gates fail (d3 + d7); plus d4 will pass deterministically.
    payload = {
        "d2": {"passed": True, "score": 0.9, "reason": "ok"},
        "d3": {"passed": False, "score": 0.2, "reason": "declarative"},
        "d6": {"passed": True, "score": 0.9, "reason": "ok"},
        "d7": {"passed": False, "score": 0.2, "reason": "leading"},
    }

    async def style_backend(prompt: str) -> str:
        return json.dumps(payload)

    async def mqm_backend(prompt: str) -> str:
        return _GOOD_MQM_PAYLOAD

    verdict = await panel.evaluate(
        good_question, llm_call=style_backend, mqm_llm_call=mqm_backend
    )
    # d2,d4,d6 pass = 3/5 soft gates; below the required 4/5.
    assert verdict.overall_pass is False


@pytest.mark.asyncio
async def test_panel_budget_partial_aggregation_soft_skips_d8(
    good_question: PanelQuestion, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If d8 exceeds ``PANEL_BUDGET_S`` the panel must still aggregate the
    other 10 judges and mark d8 as a soft-skip (passed=True) — this is
    the event-112 fix: graceful degradation instead of a whole-panel
    timeout that falls back to a mock verdict.
    """

    from polyglot_alpha.judges import panel as panel_mod
    from polyglot_alpha.judges.style_alignment import d8_duplicate_detection
    from polyglot_alpha.judges.types import JudgeResult

    # Tight panel budget so a single slow judge can blow past it without
    # the test waiting on the per-judge retry path (60s + 90s).
    monkeypatch.setattr(panel_mod, "PANEL_BUDGET_S", 1.0)
    monkeypatch.setattr(panel_mod, "PER_JUDGE_TIMEOUT_S", 5.0)
    monkeypatch.setattr(panel_mod, "PER_JUDGE_TIMEOUT_RETRY_S", 5.0)

    async def slow_d8(*args: Any, **kwargs: Any) -> JudgeResult:
        await asyncio.sleep(30.0)  # never returns within the budget
        return JudgeResult(
            name="d8_duplicate_detection",
            passed=True,
            score=1.0,
            reason="unreachable",
        )

    monkeypatch.setattr(
        d8_duplicate_detection,
        "judge_d8_duplicate_detection",
        slow_d8,
    )
    # The panel imports the symbol directly, so patch that binding too.
    monkeypatch.setattr(panel_mod, "judge_d8_duplicate_detection", slow_d8)

    async def style_backend(prompt: str) -> str:
        return json.dumps(_GOOD_STYLE_PAYLOAD)

    async def mqm_backend(prompt: str) -> str:
        return _GOOD_MQM_PAYLOAD

    verdict = await panel_mod.evaluate(
        good_question, llm_call=style_backend, mqm_llm_call=mqm_backend
    )

    # d8 must have been marked partial / soft-skip; other 10 judges must
    # still be present in the aggregated verdict.
    d8 = next(
        jr for jr in verdict.judge_results if jr.name == "d8_duplicate_detection"
    )
    assert d8.evidence.get("partial") is True
    assert d8.evidence.get("panel_budget_exceeded") is True
    assert d8.evidence.get("soft_skip") is True
    # d8 is a hard gate; soft-skip means style_alignment_passes['d8'] is True
    # so the rest of the panel can still anchor.
    assert verdict.style_alignment_passes["d8"] is True
    # The verdict must carry a "Panel partial:" note for audit.
    assert any("Panel partial" in n for n in verdict.notes)
    # And critically: this is NOT the mock verdict (mock returns
    # overall_score == 0.85 / 85 on the 0-100 scale and lacks notes).
    assert verdict.overall_score >= 0
    assert verdict.overall_score <= 100


@pytest.mark.asyncio
async def test_mqm_judge_records_provider_label() -> None:
    """MQM judge evidence must carry a provider label for audit trail."""

    q = PanelQuestion(title="Will X by 2026?")

    async def backend(prompt: str) -> str:
        return _GOOD_MQM_PAYLOAD

    result = await judge_mqm_llm(q, llm_call=backend)
    assert "provider" in result.evidence
    # Injected backends carry the 'injected' label so we can tell them
    # apart from real OpenRouter calls in the cost log.
    assert result.evidence["provider"] == "injected"


def test_duplicate_threshold_is_092_per_readme() -> None:
    """README §5.22 explicitly says cosine >= 0.92 is the duplicate cut-off."""

    from polyglot_alpha.judges.types import DUPLICATE_COSINE_THRESHOLD

    assert DUPLICATE_COSINE_THRESHOLD == pytest.approx(0.92)


def test_hard_gates_are_d1_d5_d8_per_readme() -> None:
    """Hard gates per README §5.22 aggregation rule."""

    from polyglot_alpha.judges.types import (
        HARD_STYLE_REQUIREMENTS,
        MAJORITY_REQUIRED_COUNT,
        MAJORITY_STYLE_POOL,
    )

    assert set(HARD_STYLE_REQUIREMENTS) == {"d1", "d5", "d8"}
    assert set(MAJORITY_STYLE_POOL) == {"d2", "d3", "d4", "d6", "d7"}
    assert MAJORITY_REQUIRED_COUNT == 4
