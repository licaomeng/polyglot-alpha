"""Tests for ``PolymarketV2Client`` dry-run mode.

The dry-run path is the hackathon default — it constructs the full
Gamma-API request payload but never POSTs it. These tests pin the
expected shape so the UI can rely on ``mode='dry_run'`` and a fully
populated ``payload`` dict.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_polymarket_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip live builder secrets so tests never hit the real Gamma API."""

    for key in (
        "POLYMARKET_BUILDER_API_KEY",
        "POLYMARKET_BUILDER_API_SECRET",
        "POLYMARKET_BUILDER_API_PASSPHRASE",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.asyncio
async def test_dry_run_mode_is_default() -> None:
    """When ``POLYMARKET_MODE`` is unset, the client defaults to dry_run."""

    import os

    from polyglot_alpha.polymarket.client import PolymarketV2Client
    from polyglot_alpha.polymarket.types import PolymarketMode

    os.environ.pop("POLYMARKET_MODE", None)
    async with PolymarketV2Client(builder_code="POLYGLOT_TEST") as client:
        assert client.mode == PolymarketMode.DRY_RUN


@pytest.mark.asyncio
async def test_dry_run_builds_full_gamma_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``submit_question`` in dry_run mode returns a complete Gamma payload."""

    monkeypatch.setenv("POLYMARKET_MODE", "dry_run")
    monkeypatch.setenv("POLYMARKET_BUILDER_NAME", "Polyglot Alpha")

    from polyglot_alpha.polymarket.client import PolymarketV2Client
    from polyglot_alpha.polymarket.types import PolymarketMode, Question

    question = Question(
        question_id="qid-1",
        text="Will X happen by 2026-12-31?",
        category="geopolitics",
        resolution_source="example.com",
        end_date_iso="2026-12-31T23:59:59+00:00",
    )

    async with PolymarketV2Client(builder_code="POLYGLOT_TEST") as client:
        result = await client.submit_question(question)

    assert result.is_simulated is True
    assert result.mode == PolymarketMode.DRY_RUN.value
    assert result.market_id.startswith("dryrun-")
    # The payload must contain every Gamma-API field the real submission needs.
    payload = result.payload
    assert payload["question"] == "Will X happen by 2026-12-31?"
    assert payload["category"] == "geopolitics"
    assert payload["resolution_source"] == "example.com"
    assert payload["end_date_iso"] == "2026-12-31T23:59:59+00:00"
    assert payload["builder_code"] == "POLYGLOT_TEST"
    assert payload["builder_name"] == "Polyglot Alpha"
    assert payload["external_id"] == "qid-1"
    assert payload["outcomes"] == ["Yes", "No"]
    assert payload["client_id"] == "polyglot-alpha"
    assert payload["initial_liquidity_usdc"] == 100.0


@pytest.mark.asyncio
async def test_dry_run_bypasses_quality_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run mode submits regardless of ``overall_score`` so judges can
    inspect what would have been posted even for low-quality candidates.
    """

    monkeypatch.setenv("POLYMARKET_MODE", "dry_run")
    from polyglot_alpha.polymarket.client import PolymarketV2Client
    from polyglot_alpha.polymarket.types import Question

    question = Question(question_id="qid-low", text="low-quality demo q")
    async with PolymarketV2Client(builder_code="POLYGLOT_TEST") as client:
        result = await client.submit_question(question, overall_score=0.1)
    assert result.is_simulated is True
    assert result.status == "dry_run"


@pytest.mark.asyncio
async def test_real_mode_blocked_without_confirm_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real mode without ``confirm_real_submission=True`` returns blocked."""

    monkeypatch.setenv("POLYMARKET_MODE", "real")
    from polyglot_alpha.polymarket.client import PolymarketV2Client
    from polyglot_alpha.polymarket.types import Question

    question = Question(question_id="qid-real", text="real demo q")
    async with PolymarketV2Client(builder_code="POLYGLOT_TEST") as client:
        result = await client.submit_question(
            question, confirm_real_submission=False, overall_score=0.99
        )
    assert result.is_simulated is True
    assert result.status == "blocked"
    assert "confirm_real_submission" in (result.error or "")


@pytest.mark.asyncio
async def test_real_mode_blocked_below_quality_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real mode rejects ``overall_score`` below ``REAL_QUALITY_GATE``."""

    monkeypatch.setenv("POLYMARKET_MODE", "real")
    from polyglot_alpha.polymarket.client import PolymarketV2Client, REAL_QUALITY_GATE
    from polyglot_alpha.polymarket.types import Question

    question = Question(question_id="qid-real-low", text="low-score real")
    async with PolymarketV2Client(builder_code="POLYGLOT_TEST") as client:
        result = await client.submit_question(
            question,
            confirm_real_submission=True,
            overall_score=REAL_QUALITY_GATE - 0.01,
        )
    assert result.is_simulated is True
    assert result.status == "blocked"
    assert "below" in (result.error or "").lower()
