"""Tests for the Polygon ``OrderFilled`` indexer.

Coverage:

  1. ``_parse_order_filled_log`` decodes a canonical Polygon log payload.
  2. Empty ``our_market_ids`` short-circuits — no RPC call, no fills.
  3. ``list_fills`` filters out fills for markets we don't own.
  4. ``check_connectivity`` failure → ``make_fill_indexer`` falls back
     to ``MockFillSource`` with ``is_simulated=True``.
  5. Multiple markets registered → eth_getLogs is called with all
     orderHashes in the topic[1] array, returned fills are kept.
  6. RPC-level error inside ``_rpc_call`` raises ``ValueError`` so the
     caller can short-circuit (covered by the stream_fills tick).
  7. ``MockFillSource.list_fills`` emits an ``is_simulated=True`` fill.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import httpx
import pytest

from polyglot_alpha.polymarket.fill_indexer import (
    BUILDER_FEE_RATE,
    CTF_EXCHANGE_V2,
    MockFillSource,
    ORDER_FILLED_TOPIC,
    PolygonFillIndexer,
    _parse_order_filled_log,
    make_fill_indexer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_order_filled_log(
    *,
    order_hash: str,
    maker: str = "0x" + "11" * 20,
    taker: str = "0x" + "22" * 20,
    maker_asset_id: int = 1,
    taker_asset_id: int = 2,
    maker_amount_filled: int = 5_000_000,
    taker_amount_filled: int = 100_000_000,  # 100 USDC (6 decimals)
    fee: int = 400_000,
    log_index: str = "0x1",
    block_number: str = "0x100",
) -> dict:
    """Build a Polygon ``eth_getLogs`` result entry shaped like OrderFilled.

    All amounts are encoded as 32-byte (64-hex-char) words concatenated
    into ``data``; indexed fields go into ``topics``.
    """

    def _word(value: int) -> str:
        return f"{value:064x}"

    def _topic_address(addr: str) -> str:
        return "0x" + ("0" * 24) + addr.lower().removeprefix("0x")

    def _topic_hash(h: str) -> str:
        h = h.lower().removeprefix("0x")
        return "0x" + h.rjust(64, "0")

    data = "0x" + "".join(
        [
            _word(maker_asset_id),
            _word(taker_asset_id),
            _word(maker_amount_filled),
            _word(taker_amount_filled),
            _word(fee),
        ]
    )
    return {
        "address": CTF_EXCHANGE_V2,
        "topics": [
            ORDER_FILLED_TOPIC,
            _topic_hash(order_hash),
            _topic_address(maker),
            _topic_address(taker),
        ],
        "data": data,
        "blockNumber": block_number,
        "logIndex": log_index,
    }


class _MockTransport(httpx.AsyncBaseTransport):
    """Tiny httpx transport that returns canned JSON-RPC responses.

    Pass a list of response *bodies* (dicts); each request pops the
    next one. Also records every request payload so tests can assert
    the right method/params were sent.
    """

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.requests: list[dict] = []

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if not self._responses:
            raise AssertionError("MockTransport exhausted")
        payload = self._responses.pop(0)
        return httpx.Response(200, json=payload)


# ---------------------------------------------------------------------------
# 1. parse OrderFilled log
# ---------------------------------------------------------------------------


def test_parse_order_filled_log_decodes_taker_amount() -> None:
    order_hash = "0x" + "ab" * 32
    log_entry = _make_order_filled_log(
        order_hash=order_hash,
        taker_amount_filled=100_000_000,  # 100 USDC
    )
    fill = _parse_order_filled_log(log_entry)
    assert fill is not None
    assert fill.market_id == order_hash
    assert fill.fill_amount_usdc == pytest.approx(100.0, rel=1e-9)
    assert fill.builder_fee_usdc == pytest.approx(
        100.0 * BUILDER_FEE_RATE, rel=1e-9
    )
    assert fill.is_simulated is False
    assert fill.taker_address.startswith("0x")
    assert len(fill.taker_address) == 42


def test_parse_order_filled_log_rejects_wrong_topic() -> None:
    log_entry = _make_order_filled_log(order_hash="0x" + "01" * 32)
    log_entry["topics"][0] = "0x" + "ff" * 32  # garbage
    assert _parse_order_filled_log(log_entry) is None


def test_parse_order_filled_log_rejects_zero_amount() -> None:
    log_entry = _make_order_filled_log(
        order_hash="0x" + "01" * 32, taker_amount_filled=0
    )
    assert _parse_order_filled_log(log_entry) is None


# ---------------------------------------------------------------------------
# 2. empty market list short-circuits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_market_list_yields_no_fills() -> None:
    transport = _MockTransport([])  # would crash if called
    async with httpx.AsyncClient(transport=transport) as http:
        indexer = PolygonFillIndexer(
            rpc_url="http://test/", http_client=http
        )
        # No register_market call.
        fills = await indexer.list_fills("0xdeadbeef", since_ts=0)
    assert fills == []
    assert transport.requests == []


# ---------------------------------------------------------------------------
# 3. happy path — register, fetch, parse, filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_fills_returns_canned_log() -> None:
    market_id = "0x" + "ab" * 32
    canned_log = _make_order_filled_log(
        order_hash=market_id, taker_amount_filled=250_000_000
    )
    responses = [
        {"jsonrpc": "2.0", "id": 1, "result": "0x10"},  # eth_blockNumber
        {"jsonrpc": "2.0", "id": 2, "result": [canned_log]},  # eth_getLogs
    ]
    transport = _MockTransport(responses)
    async with httpx.AsyncClient(transport=transport) as http:
        indexer = PolygonFillIndexer(
            rpc_url="http://test/", http_client=http
        )
        indexer.register_market(market_id)
        fills = await indexer.list_fills(market_id, since_ts=0)

    assert len(fills) == 1
    assert fills[0].market_id == market_id
    assert fills[0].fill_amount_usdc == pytest.approx(250.0)
    assert fills[0].is_simulated is False
    # Two RPC calls in order.
    assert [r["method"] for r in transport.requests] == [
        "eth_blockNumber",
        "eth_getLogs",
    ]
    # The eth_getLogs filter must scope to our contract and our orderHash.
    log_filter = transport.requests[1]["params"][0]
    assert log_filter["address"] == CTF_EXCHANGE_V2.lower()
    assert log_filter["topics"][0] == ORDER_FILLED_TOPIC
    assert market_id.lower() in [t.lower() for t in log_filter["topics"][1]]


# ---------------------------------------------------------------------------
# 4. RPC failure → make_fill_indexer falls back to mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_make_fill_indexer_falls_back_when_rpc_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail(self) -> bool:  # type: ignore[no-untyped-def]
        return False

    monkeypatch.setattr(
        PolygonFillIndexer, "check_connectivity", _fail, raising=True
    )
    source = await make_fill_indexer(market_id="0xabc")
    assert isinstance(source, MockFillSource)
    assert source.is_simulated is True


@pytest.mark.asyncio
async def test_make_fill_indexer_force_mock() -> None:
    source = await make_fill_indexer(market_id="0xabc", force_mock=True)
    assert isinstance(source, MockFillSource)
    assert source.is_simulated is True


# ---------------------------------------------------------------------------
# 5. multiple markets → all orderHashes in topic filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_markets_filter_in_request() -> None:
    market_a = "0x" + "aa" * 32
    market_b = "0x" + "bb" * 32

    log_a = _make_order_filled_log(
        order_hash=market_a, taker_amount_filled=50_000_000
    )
    log_b = _make_order_filled_log(
        order_hash=market_b, taker_amount_filled=75_000_000, log_index="0x2"
    )
    responses = [
        {"jsonrpc": "2.0", "id": 1, "result": "0x100"},
        {"jsonrpc": "2.0", "id": 2, "result": [log_a, log_b]},
        # Second list_fills tick advances cursor — head must move past last_block_seen
        {"jsonrpc": "2.0", "id": 3, "result": "0x200"},
        {"jsonrpc": "2.0", "id": 4, "result": [log_b]},
    ]
    transport = _MockTransport(responses)
    async with httpx.AsyncClient(transport=transport) as http:
        indexer = PolygonFillIndexer(
            rpc_url="http://test/", http_client=http
        )
        indexer.register_market(market_a)
        indexer.register_market(market_b)
        fills_a = await indexer.list_fills(market_a, since_ts=0)
        fills_b = await indexer.list_fills(market_b, since_ts=0)

    # First call returned both; we only kept market_a-matching ones.
    assert len(fills_a) == 1
    assert fills_a[0].market_id == market_a
    # Second tick returned [log_b]; filter keeps it.
    assert len(fills_b) == 1
    assert fills_b[0].market_id == market_b

    topic_filter = transport.requests[1]["params"][0]["topics"][1]
    topic_filter_lower = [t.lower() for t in topic_filter]
    assert market_a.lower() in topic_filter_lower
    assert market_b.lower() in topic_filter_lower


# ---------------------------------------------------------------------------
# 6. unregistered market in list_fills returns []
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_fills_unregistered_market_returns_empty() -> None:
    market_id = "0x" + "cc" * 32
    other = "0x" + "dd" * 32
    transport = _MockTransport([])  # MUST NOT be called
    async with httpx.AsyncClient(transport=transport) as http:
        indexer = PolygonFillIndexer(
            rpc_url="http://test/", http_client=http
        )
        indexer.register_market(market_id)
        # Ask for a different market — short-circuits before RPC.
        fills = await indexer.list_fills(other, since_ts=0)
    assert fills == []
    assert transport.requests == []


# ---------------------------------------------------------------------------
# 7. MockFillSource emits is_simulated=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_fill_source_emits_simulated() -> None:
    source = MockFillSource()
    source.register_market("market-1")
    fills = await source.list_fills("market-1", since_ts=0)
    assert len(fills) == 1
    assert fills[0].is_simulated is True
    assert fills[0].builder_fee_usdc > 0


# ---------------------------------------------------------------------------
# 8. check_connectivity success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_connectivity_success() -> None:
    transport = _MockTransport(
        [{"jsonrpc": "2.0", "id": 1, "result": "0x1234"}]
    )
    async with httpx.AsyncClient(transport=transport) as http:
        indexer = PolygonFillIndexer(
            rpc_url="http://test/", http_client=http
        )
        assert await indexer.check_connectivity() is True


@pytest.mark.asyncio
async def test_check_connectivity_handles_http_error() -> None:
    class _BoomTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(
            self, request: httpx.Request
        ) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=_BoomTransport()) as http:
        indexer = PolygonFillIndexer(
            rpc_url="http://test/", http_client=http
        )
        assert await indexer.check_connectivity() is False
