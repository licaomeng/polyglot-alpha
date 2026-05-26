"""W14-FIX-STUB end-to-end verification.

Monkey-patches the LLM call to always return an empty string (the most
common LLM-glitch failure mode) and walks an event through the full
analysts -> translators -> synthesizer -> quality_eval -> polymarket
pipeline. Verifies every stub gate fires:

    1. ``translators.propose_candidates`` emits a ``logger.warning`` and
       sets ``candidate.is_stub = True``.
    2. ``analysts._parse_response`` emits a ``logger.warning`` for
       missing ``JSON:`` marker.
    3. ``synthesizer.synthesize`` propagates ``is_stub`` to the
       :class:`Question`.
    4. ``quality_eval.score_question`` returns ``score=0.0`` and
       ``passed=False`` with ``"stub_detected"`` in the rationale.
    5. ``polymarket.client._build_gamma_payload`` raises ``ValueError``
       before any HTTP traffic.

Run::

    python scripts/test_stub_blocking.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import List

# Force-import everything we need from the package.
from polyglot_alpha import analysts, quality_eval, synthesizer, translators
from polyglot_alpha.polymarket.client import _build_gamma_payload
from polyglot_alpha.polymarket.types import Question as PMQuestion
from polyglot_alpha.schemas import NewsEvent
from polyglot_alpha.stub_detector import is_stub


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


class WarningCollector(logging.Handler):
    """Capture WARNING+ records so we can assert what was logged."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages_for(self, logger_name: str) -> List[str]:
        return [r.getMessage() for r in self.records if r.name == logger_name]


def _install_collector() -> WarningCollector:
    handler = WarningCollector()
    root = logging.getLogger("polyglot_alpha")
    root.setLevel(logging.WARNING)
    root.addHandler(handler)
    return handler


async def _broken_llm(prompt: str) -> str:
    """The 'broken LLM' fixture: returns empty string regardless of prompt."""
    return ""


# --------------------------------------------------------------------------- #
# Test body                                                                   #
# --------------------------------------------------------------------------- #


async def _run() -> int:
    collector = _install_collector()

    event = NewsEvent(
        event_id="evt-stub-blocking-test",
        url="https://example.com/news/stub-test",
        title_zh="测试事件 — LLM glitch simulation",
        body_zh="此事件被故意触发以验证 stub-blocking 链。",
        cutoff_ts=1_800_000_000,
    )

    # ----- Layer 1: analysts. Empty LLM -> empty entities/risks + WARNING.
    reports = await analysts.run_analysts(event, _broken_llm)
    assert len(reports) >= 1, "expected at least one analyst report"
    for r in reports:
        assert r.relevant_entities == [], r.relevant_entities
        assert r.risk_factors == [], r.risk_factors
    analyst_warns = collector.messages_for("polyglot_alpha.analysts")
    assert any("JSON parse failed" in m or "missing 'JSON:' marker" in m for m in analyst_warns), (
        f"analysts: expected a JSON-parse warning, got: {analyst_warns!r}"
    )
    print("[1/5] analysts: emitted parse-fail WARNING (count=%d)" % len(analyst_warns))

    # ----- Layer 2: translators. Empty LLM -> is_stub=True + WARNING.
    candidates = await translators.propose_candidates(event, reports, _broken_llm, n=2)
    assert len(candidates) == 2
    for c in candidates:
        assert getattr(c, "is_stub", False), (
            f"translators: expected is_stub=True on candidate {c.translator_id}"
        )
        # Stub strings must come through verbatim so quality_eval can catch them.
        assert is_stub(c.question_en) or is_stub(c.resolution_criteria), (
            f"translators: expected stub text on {c.translator_id}, got {c.question_en!r}"
        )
    translator_warns = collector.messages_for("polyglot_alpha.translators")
    assert any("falling back to stub" in m for m in translator_warns), (
        f"translators: expected fallback WARNING, got: {translator_warns!r}"
    )
    print("[2/5] translators: emitted stub-fallback WARNING + is_stub=True on both candidates")

    # ----- Layer 3: synthesizer. is_stub must propagate to the Question.
    question = synthesizer.synthesize(event, candidates)
    assert getattr(question, "is_stub", False), (
        "synthesizer: expected is_stub=True propagated to Question"
    )
    print("[3/5] synthesizer: propagated is_stub=True to Question")

    # ----- Layer 4: quality_eval. score=0.0, passed=False, reason contains stub_detected.
    qs = quality_eval.score_question(question)
    assert qs.score == 0.0, f"quality_eval: expected score=0.0, got {qs.score}"
    assert qs.passed is False, "quality_eval: expected passed=False"
    assert "stub_detected" in qs.rationale, (
        f"quality_eval: expected 'stub_detected' in rationale, got {qs.rationale!r}"
    )
    print(f"[4/5] quality_eval: score=0.0, passed=False, rationale={qs.rationale!r}")

    # ----- Layer 5: polymarket. _build_gamma_payload must raise BEFORE any HTTP.
    pm_question = PMQuestion(
        question_id="q-stub-blocking-test",
        text=question.question_en,
        end_date_iso=question.end_date_iso,
    )
    try:
        _build_gamma_payload(pm_question, "builder-test", None)
    except ValueError as exc:
        assert "stub" in str(exc).lower(), f"polymarket: unexpected error: {exc}"
        print(f"[5/5] polymarket: refused to build Gamma payload — {exc}")
    else:
        print("[5/5] FAIL: polymarket did NOT raise on stub question!", file=sys.stderr)
        return 1

    # --- Summary --------------------------------------------------------- #
    print()
    print("=" * 72)
    print("ALL 5 STUB GATES FIRED CORRECTLY")
    print(f"  analysts WARNINGs:    {len(analyst_warns)}")
    print(f"  translators WARNINGs: {len(translator_warns)}")
    print(f"  synthesizer is_stub:  {getattr(question, 'is_stub', False)}")
    print(f"  quality_eval score:   {qs.score} (passed={qs.passed})")
    print(f"  polymarket payload:   blocked with ValueError")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
