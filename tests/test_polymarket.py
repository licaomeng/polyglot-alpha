"""Tests for the polymarket subpackage.

Covers:
  * Mock client deterministic stream
  * Real-mode happy path (httpx mocked)
  * Real-mode network failure falls back to mock + labels result
  * Mode toggle via env var
  * Builder code registration round-trip (demo + real)
  * FillListener calls recordFill with correct args + dedupes fills
  * FillListener emits SSE + DB events with correct shape
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from polyglot_alpha.polymarket import (
    BuilderFeeEvent,
    Fill,
    MockPolymarketClient,
    PolymarketMode,
    PolymarketV2Client,
    Question,
    register_builder_code,
    resolve_translator_for_code,
)
from polyglot_alpha.polymarket.fill_listener import FillListener


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def question() -> Question:
    return Question(
        question_id="q-1",
        text="Will GPT-5 ship before 2027?",
        category="ai",
        resolution_source="https://openai.com",
        end_date_iso="2026-12-31T23:59:59Z",
        initial_liquidity_usdc=500.0,
    )


@pytest.fixture
def translator_address() -> str:
    return "0xAbCdEf0000000000000000000000000000000001"


@pytest.fixture(autouse=True)
def isolated_builder_registry(tmp_path, monkeypatch):
    """Each test gets its own builder-code registry file."""
    target = tmp_path / "builder_codes.json"
    monkeypatch.setenv("POLYGLOT_BUILDER_REGISTRY_PATH", str(target))
    yield target


# ---------------------------------------------------------------------------
# MockPolymarketClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_client_submit_and_fills_are_deterministic(question):
    # Same seed -> identical streams. Demonstrates reproducibility for demos.
    fake_time = [1_700_000_000]

    def now() -> int:
        return fake_time[0]

    a = MockPolymarketClient("CODE1", seed=42, time_fn=now)
    b = MockPolymarketClient("CODE1", seed=42, time_fn=now)

    submit_a = await a.submit_question(question)
    submit_b = await b.submit_question(question)
    assert submit_a.is_simulated is True
    assert submit_b.is_simulated is True
    assert submit_a.status == "submitted"
    assert submit_a.polymarket_url.startswith("https://polymarket.com/market/")
    assert submit_a.fees_estimate_usdc == pytest.approx(500.0 * 0.004)

    # Advance synthetic time by 2 minutes so a fills batch can land.
    fake_time[0] += 120
    fills_a = await a.list_fills(submit_a.market_id, since_ts=now() - 120)
    fills_b = await b.list_fills(submit_b.market_id, since_ts=now() - 120)
    assert [f.fill_amount_usdc for f in fills_a] == [
        f.fill_amount_usdc for f in fills_b
    ]
    for f in fills_a:
        assert 50.0 <= f.fill_amount_usdc <= 500.0
        assert f.builder_fee_usdc == pytest.approx(f.fill_amount_usdc * 0.004)
        assert f.is_simulated is True

    await a.close()
    await b.close()


# ---------------------------------------------------------------------------
# PolymarketV2Client — real mode happy path & fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_mode_submit_question_happy_path(question, monkeypatch):
    """Real mode hits Gamma; we mock the transport with httpx.MockTransport."""

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": "0xMARKET",
                "url": "https://polymarket.com/event/test",
                "status": "submitted",
                "fees_estimate_usdc": 2.5,
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, timeout=5.0)
    client = PolymarketV2Client(
        builder_code="BUILDER1",
        api_key="key",
        mode=PolymarketMode.REAL,
        http_client=http,
    )
    # Real-mode now requires builder secrets + explicit
    # ``confirm_real_submission=True`` for safety. Inject the secrets
    # so the call is allowed to hit the mocked HTTP transport.
    import os as _os

    _os.environ["POLYMARKET_BUILDER_API_KEY"] = "test-key"
    _os.environ["POLYMARKET_BUILDER_API_SECRET"] = "test-secret"
    _os.environ["POLYMARKET_BUILDER_API_PASSPHRASE"] = "test-pass"
    try:
        result = await client.submit_question(
            question, confirm_real_submission=True
        )
    finally:
        for k in (
            "POLYMARKET_BUILDER_API_KEY",
            "POLYMARKET_BUILDER_API_SECRET",
            "POLYMARKET_BUILDER_API_PASSPHRASE",
        ):
            _os.environ.pop(k, None)

    assert result.market_id == "0xMARKET"
    assert result.is_simulated is False
    assert result.polymarket_url == "https://polymarket.com/event/test"
    assert result.fees_estimate_usdc == pytest.approx(2.5)
    assert "/markets" in captured["url"]
    assert "BUILDER1" in captured["body"]

    await client.close()


@pytest.mark.asyncio
async def test_real_mode_falls_back_to_mock_on_network_error(question):
    """If the real API errors out we must degrade to dry_run and label it."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated outage")

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, timeout=1.0)
    client = PolymarketV2Client(
        builder_code="BUILDER1",
        mode=PolymarketMode.REAL,
        http_client=http,
    )
    import os as _os

    _os.environ["POLYMARKET_BUILDER_API_KEY"] = "test-key"
    _os.environ["POLYMARKET_BUILDER_API_SECRET"] = "test-secret"
    _os.environ["POLYMARKET_BUILDER_API_PASSPHRASE"] = "test-pass"
    try:
        result = await client.submit_question(
            question, confirm_real_submission=True
        )
    finally:
        for k in (
            "POLYMARKET_BUILDER_API_KEY",
            "POLYMARKET_BUILDER_API_SECRET",
            "POLYMARKET_BUILDER_API_PASSPHRASE",
        ):
            _os.environ.pop(k, None)

    assert result.is_simulated is True
    assert result.error is not None
    assert "real_api_unavailable" in result.error

    await client.close()


# ---------------------------------------------------------------------------
# Mode toggle
# ---------------------------------------------------------------------------


def test_mode_defaults_to_dry_run_when_env_unset(monkeypatch):
    monkeypatch.delenv("POLYMARKET_MODE", raising=False)
    client = PolymarketV2Client(builder_code="X")
    assert client.mode == PolymarketMode.DRY_RUN


def test_mode_honors_env_real(monkeypatch):
    monkeypatch.setenv("POLYMARKET_MODE", "real")
    client = PolymarketV2Client(builder_code="X")
    assert client.mode == PolymarketMode.REAL


def test_mode_invalid_env_falls_back_to_dry_run(monkeypatch):
    monkeypatch.setenv("POLYMARKET_MODE", "production")
    client = PolymarketV2Client(builder_code="X")
    assert client.mode == PolymarketMode.DRY_RUN


# ---------------------------------------------------------------------------
# Builder code registry
# ---------------------------------------------------------------------------


def test_register_builder_code_demo_mode_is_deterministic(translator_address):
    code1 = register_builder_code(translator_address)
    code2 = register_builder_code(translator_address)
    assert code1 == code2
    assert len(code1) == 10
    assert resolve_translator_for_code(code1) == translator_address.lower()


def test_register_builder_code_real_mode_preserves_code(translator_address):
    code = register_builder_code(
        translator_address, real_code="POLYGLOT_ALPHA_BUILDER_V1"
    )
    assert code == "POLYGLOT_ALPHA_BUILDER_V1"
    assert resolve_translator_for_code(code) == translator_address.lower()


def test_register_builder_code_rejects_bad_address():
    with pytest.raises(ValueError):
        register_builder_code("not-an-address")


# ---------------------------------------------------------------------------
# FillListener
# ---------------------------------------------------------------------------


class _RecordingChain:
    """Minimal stand-in for ChainRecorder — records calls + returns tx hashes."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_for = fail_for or set()
        self._counter = 0

    async def record_fill(
        self,
        *,
        market_id: str,
        fill_amount_usdc: float,
        translator_address: str,
    ) -> str | None:
        self.calls.append(
            {
                "market_id": market_id,
                "fill_amount_usdc": fill_amount_usdc,
                "translator_address": translator_address,
            }
        )
        if market_id in self._fail_for:
            raise RuntimeError("chain boom")
        self._counter += 1
        return f"0x{self._counter:064x}"


class _ScriptedFillSource:
    """Returns predefined fill batches per poll call."""

    def __init__(self, batches: list[list[Fill]]) -> None:
        self._batches = list(batches)
        self.requests: list[tuple[str, int]] = []

    async def list_fills(self, market_id: str, since_ts: int) -> list[Fill]:
        self.requests.append((market_id, since_ts))
        if not self._batches:
            return []
        return self._batches.pop(0)


@pytest.mark.asyncio
async def test_fill_listener_records_each_fill_with_correct_args(
    translator_address,
):
    fills = [
        Fill(
            fill_id="f1",
            market_id="m-1",
            fill_amount_usdc=100.0,
            builder_fee_usdc=0.4,
            timestamp=1_700_000_010,
            is_simulated=True,
        ),
        Fill(
            fill_id="f2",
            market_id="m-1",
            fill_amount_usdc=250.0,
            builder_fee_usdc=1.0,
            timestamp=1_700_000_020,
            is_simulated=True,
        ),
    ]
    source = _ScriptedFillSource([fills, []])
    chain = _RecordingChain()
    sse_events: list[dict] = []
    db_rows: list[BuilderFeeEvent] = []

    async def sse(event: dict) -> None:
        sse_events.append(event)

    async def db(event: BuilderFeeEvent) -> None:
        db_rows.append(event)

    listener = FillListener(
        client=source,
        market_id="m-1",
        translator_address=translator_address,
        sse_sink=sse,
        db_sink=db,
        chain_recorder=chain,
        time_fn=lambda: 1_700_000_000,
    )

    events = await listener.poll_once()

    assert [c["market_id"] for c in chain.calls] == ["m-1", "m-1"]
    assert [c["fill_amount_usdc"] for c in chain.calls] == [0.4, 1.0]
    assert all(c["translator_address"] == translator_address for c in chain.calls)
    assert len(events) == 2
    assert all(e.on_chain_status == "confirmed" for e in events)
    assert all(e.tx_hash and e.tx_hash.startswith("0x") for e in events)
    assert [r.fill_id for r in db_rows] == ["f1", "f2"]
    assert [e["type"] for e in sse_events] == [
        "builder_fee.accrued",
        "builder_fee.accrued",
    ]
    assert sse_events[0]["data"]["fill_id"] == "f1"
    assert sse_events[0]["data"]["is_simulated"] is True

    # Second poll yields nothing — cursor advanced past last fill ts.
    follow_up = await listener.poll_once()
    assert follow_up == []


@pytest.mark.asyncio
async def test_fill_listener_dedupes_repeated_fill_ids(translator_address):
    repeated = Fill(
        fill_id="dup-1",
        market_id="m-9",
        fill_amount_usdc=80.0,
        builder_fee_usdc=0.32,
        timestamp=1_700_000_050,
        is_simulated=True,
    )
    # Same fill id served twice — listener must only record once.
    source = _ScriptedFillSource([[repeated], [repeated]])
    chain = _RecordingChain()
    listener = FillListener(
        client=source,
        market_id="m-9",
        translator_address=translator_address,
        chain_recorder=chain,
        time_fn=lambda: 1_700_000_000,
    )
    first = await listener.poll_once()
    second = await listener.poll_once()
    assert len(first) == 1
    assert second == []
    assert len(chain.calls) == 1


@pytest.mark.asyncio
async def test_fill_listener_marks_chain_failures(translator_address):
    fill = Fill(
        fill_id="ferr",
        market_id="m-err",
        fill_amount_usdc=120.0,
        builder_fee_usdc=0.48,
        timestamp=1_700_000_100,
        is_simulated=True,
    )
    source = _ScriptedFillSource([[fill]])
    chain = _RecordingChain(fail_for={"m-err"})
    listener = FillListener(
        client=source,
        market_id="m-err",
        translator_address=translator_address,
        chain_recorder=chain,
        time_fn=lambda: 1_700_000_000,
    )
    events = await listener.poll_once()
    assert len(events) == 1
    assert events[0].on_chain_status == "failed"
    assert events[0].tx_hash is None


@pytest.mark.asyncio
async def test_fill_listener_without_chain_marks_skipped(translator_address):
    fill = Fill(
        fill_id="fnochain",
        market_id="m-x",
        fill_amount_usdc=60.0,
        builder_fee_usdc=0.24,
        timestamp=1_700_000_200,
        is_simulated=True,
    )
    source = _ScriptedFillSource([[fill]])
    listener = FillListener(
        client=source,
        market_id="m-x",
        translator_address=translator_address,
        time_fn=lambda: 1_700_000_000,
    )
    events = await listener.poll_once()
    assert len(events) == 1
    assert events[0].on_chain_status == "skipped"
    assert events[0].tx_hash is None
