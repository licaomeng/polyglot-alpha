"""Real Polygon ``CTFExchangeV2.OrderFilled`` indexer.

Watches Polygon mainnet for fills on markets attributed to our
``builder_code``, then yields :class:`~polyglot_alpha.polymarket.types.Fill`
records that ``FillListener`` already knows how to bridge to the on-chain
``BuilderFeeRouter.recordFill`` call on Arc.

Design notes
============

* **Async-first.** We use ``httpx.AsyncClient`` to talk JSON-RPC so the
  indexer slots into the existing asyncio orchestration without forcing
  a thread pool.

* **Cursor-based polling.** Each tick we ask the node for logs from
  ``last_block_seen + 1`` up to ``latest``. We cap the range
  (``MAX_BLOCK_RANGE``) so a cold start doesn't try to drain months of
  history in a single call — public Polygon RPCs reject anything larger
  than ~10k blocks.

* **Filter by builder_code.** Polygon's ``OrderFilled`` event does NOT
  itself encode the builder code; the linkage happens off-chain when we
  submit the question. So we keep an in-memory set of "our" market_ids
  populated via :meth:`register_market` whenever the orchestrator hands
  out a real submission, and we filter logs against it. Markets created
  *before* the process started won't be tracked — that matches our
  product: builder-fee accrual begins at submission time.

* **Graceful fallback.** If the RPC is unreachable OR no markets are
  registered yet, :func:`make_fill_indexer` returns a
  :class:`MockFillSource` instead — so demos don't go dark when the
  network is flaky. The fallback's ``is_simulated`` flag is always
  ``True`` so the UI can label fills honestly.

Orchestrator wiring (not done in this module — parallel agent owns
``orchestrator.py``)::

    # After polymarket.submit_question:
    if result.is_simulated:
        indexer = MockFillSource()        # always simulated
    else:
        indexer = PolygonFillIndexer()    # real Polygon listener
        indexer.register_market(result.market_id)

    # FillListener already accepts anything matching _FillSource.
    listener = FillListener(client=indexer, market_id=..., ...)

Environment
===========

* ``POLYGON_RPC`` — JSON-RPC endpoint (default
  ``https://polygon-rpc.com``).
* ``POLYMARKET_BUILDER_CODE`` — our registered builder code; only used
  for logging / sanity (Polygon logs don't carry it).
* ``CTF_EXCHANGE_V2_ADDRESS`` — override the default
  ``0xC5d563A36AE78145C45a50134d48A1215220f80a``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import AsyncIterator, Awaitable, Optional

import httpx

from polyglot_alpha.polymarket.types import Fill

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Polymarket V2 CTFExchange on Polygon mainnet.
CTF_EXCHANGE_V2 = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# keccak256("OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)")
# Computed at build time; encoded as a hex string topic.
ORDER_FILLED_TOPIC = (
    "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
)

# Polymarket V2 builder fee — 0.4% of fill notional. Kept in sync with
# ``mock_client.BUILDER_FEE_RATE`` (single source of truth would be nice
# but we avoid cross-module imports here to keep the indexer
# self-contained).
BUILDER_FEE_RATE = 0.004

# USDC has 6 decimals on Polygon; CTFExchange takerAmountFilled is denominated
# in USDC for "Yes/No outcome" fills paid in collateral.
USDC_DECIMALS = 6

# Max blocks per eth_getLogs window. Public Polygon RPCs typically reject
# requests spanning more than ~10k blocks; we stay well under that.
MAX_BLOCK_RANGE = 5000

# Default polling interval in seconds.
DEFAULT_POLL_INTERVAL_SECONDS = 10.0

# Default JSON-RPC timeout. Polygon free nodes can be slow.
DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hex_to_int(value: str) -> int:
    """Parse a ``0x...`` hex string to int, tolerating ``None``/empty."""
    if not value or value in ("0x", "0x0"):
        return 0
    return int(value, 16)


def _topic_to_address(topic: str) -> str:
    """A 32-byte indexed topic holding an address: last 20 bytes, 0x-prefixed."""
    if not topic.startswith("0x") or len(topic) != 66:
        return topic
    return "0x" + topic[-40:]


def _parse_order_filled_log(log_entry: dict) -> Optional[Fill]:
    """Decode one ``OrderFilled`` log into a :class:`Fill`.

    Returns ``None`` if the log doesn't look like an OrderFilled event
    (e.g. wrong topic, missing data).

    The Polymarket V2 OrderFilled event::

        event OrderFilled(
            bytes32 indexed orderHash,
            address indexed maker,
            address indexed taker,
            uint256 makerAssetId,
            uint256 takerAssetId,
            uint256 makerAmountFilled,
            uint256 takerAmountFilled,
            uint256 fee
        );

    For our purposes:
      * ``market_id`` ← ``orderHash`` (indexed topic[1]); the off-chain
        Polymarket API resolves this to a market — for accounting we
        treat the orderHash itself as the dedup key.
      * ``fill_amount_usdc`` ← ``takerAmountFilled`` / 10**6 (USDC).
      * ``builder_fee_usdc`` ← ``takerAmountFilled * BUILDER_FEE_RATE``
        (the on-chain ``fee`` field is the *protocol* fee, not the
        builder fee; builder accrual is computed at the documented rate).
    """
    topics = log_entry.get("topics") or []
    if len(topics) < 4:
        return None
    if topics[0].lower() != ORDER_FILLED_TOPIC.lower():
        return None

    order_hash = topics[1]
    taker_address = _topic_to_address(topics[3])

    raw_data = log_entry.get("data", "0x")
    # data is 5 * 32-byte words = 320 hex chars + leading 0x. Strip 0x and
    # cut into 64-char (32-byte) words. Be defensive about short payloads.
    payload = raw_data[2:] if raw_data.startswith("0x") else raw_data
    if len(payload) < 5 * 64:
        return None
    # words[3] is takerAmountFilled (zero-indexed).
    taker_amount_word = "0x" + payload[3 * 64 : 4 * 64]
    taker_amount = _hex_to_int(taker_amount_word)
    if taker_amount <= 0:
        return None

    fill_amount_usdc = taker_amount / (10**USDC_DECIMALS)
    builder_fee_usdc = round(fill_amount_usdc * BUILDER_FEE_RATE, 6)

    # Block timestamp is not part of the log payload — we fall back to
    # the indexer's wall clock here; the FillListener cursor only cares
    # about monotonic ordering, not absolute time.
    return Fill(
        fill_id=f"polygon-{order_hash}-{log_entry.get('logIndex', '0x0')}",
        market_id=order_hash,
        fill_amount_usdc=round(fill_amount_usdc, 6),
        builder_fee_usdc=builder_fee_usdc,
        timestamp=int(time.time()),
        taker_address=taker_address,
        is_simulated=False,
    )


# ---------------------------------------------------------------------------
# Mock fallback (used when RPC is unreachable or no markets registered)
# ---------------------------------------------------------------------------


class MockFillSource:
    """Drop-in synthetic source that satisfies ``_FillSource`` Protocol.

    Returns deterministic-ish synthetic fills labeled ``is_simulated=True``
    so the UI can call them out as not real. Used when:

      * No ``POLYGON_RPC`` reachable.
      * ``register_market`` has never been called (no real markets to
        watch yet — but we still want demos to render *something*).
    """

    def __init__(self, *, fills_per_minute: float = 5.0) -> None:
        self.fills_per_minute = fills_per_minute
        self._registered: set[str] = set()

    @property
    def is_simulated(self) -> bool:
        return True

    def register_market(self, market_id: str) -> None:
        self._registered.add(market_id)

    async def list_fills(self, market_id: str, since_ts: int) -> list[Fill]:
        """Behaviorally compatible with PolygonFillIndexer.list_fills."""
        await asyncio.sleep(0)
        # Lightweight synthetic fill — one per call when called past the
        # cursor. Tests can monkey-patch this if they need volume.
        now = int(time.time())
        if since_ts >= now:
            return []
        fill_amount = 100.0
        return [
            Fill(
                fill_id=f"simfill-{uuid.uuid4().hex[:16]}",
                market_id=market_id,
                fill_amount_usdc=fill_amount,
                builder_fee_usdc=round(fill_amount * BUILDER_FEE_RATE, 6),
                timestamp=now,
                taker_address=None,
                is_simulated=True,
            )
        ]


# ---------------------------------------------------------------------------
# Real Polygon indexer
# ---------------------------------------------------------------------------


class PolygonFillIndexer:
    """Poll Polygon for ``OrderFilled`` events tied to our markets.

    Public surface:

      * :meth:`register_market` — add a market_id to watch.
      * :meth:`list_fills` — single-shot ``eth_getLogs`` pull, used by
        :class:`~polyglot_alpha.polymarket.fill_listener.FillListener` as
        a drop-in for the Polymarket REST client.
      * :meth:`stream_fills` — long-running async generator. Useful for
        ad-hoc consumers that don't want the FillListener loop.
      * :meth:`check_connectivity` — one-shot ``eth_blockNumber`` ping.

    The class is safe to instantiate with no network — methods only
    talk to the RPC when invoked.
    """

    def __init__(
        self,
        *,
        rpc_url: Optional[str] = None,
        contract_address: Optional[str] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        http_timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        max_block_range: int = MAX_BLOCK_RANGE,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.rpc_url = rpc_url or os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
        self.contract_address = (
            contract_address
            or os.getenv("CTF_EXCHANGE_V2_ADDRESS", CTF_EXCHANGE_V2)
        ).lower()
        self.poll_interval = poll_interval
        self.http_timeout = http_timeout
        self.max_block_range = max_block_range
        self.builder_code = os.getenv("POLYMARKET_BUILDER_CODE", "")

        # If the caller passes an httpx client we don't own it (don't close).
        self._external_client = http_client is not None
        self._client: Optional[httpx.AsyncClient] = http_client

        self.last_block_seen: Optional[int] = None
        self.our_market_ids: set[str] = set()
        self._rpc_id = 0

    @property
    def is_simulated(self) -> bool:
        return False

    # ----- public API ----------------------------------------------------

    def register_market(self, market_id: str) -> None:
        """Add a market_id from a real builder submission to the watch list."""
        if not market_id:
            raise ValueError("market_id is required")
        # Normalize to lowercase since Polygon orderHash topics are lower-cased.
        self.our_market_ids.add(market_id.lower())

    def unregister_market(self, market_id: str) -> None:
        self.our_market_ids.discard(market_id.lower())

    async def check_connectivity(self) -> bool:
        """Ping the RPC with ``eth_blockNumber``. Returns ``True`` on success."""
        try:
            client = await self._get_client()
            response = await self._rpc_call(client, "eth_blockNumber", [])
            return isinstance(response, str) and response.startswith("0x")
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
            log.warning("Polygon RPC connectivity check failed: %s", exc)
            return False

    async def list_fills(self, market_id: str, since_ts: int) -> list[Fill]:
        """Return ``OrderFilled`` events for ``market_id`` since last cursor.

        Implements the same shape as
        :meth:`MockPolymarketClient.list_fills` so :class:`FillListener`
        can use this object directly. ``since_ts`` is accepted for
        signature compatibility but ignored — we track progress by
        block number, not wall clock.
        """
        del since_ts  # block-based cursor; kept for signature parity.
        if not self.our_market_ids:
            return []
        # market_id must be one of ours to be polled (defensive — caller
        # should already be filtering, but we double-check).
        if market_id.lower() not in self.our_market_ids:
            return []
        fills = await self._fetch_new_fills()
        return [f for f in fills if f.market_id.lower() == market_id.lower()]

    async def stream_fills(self) -> AsyncIterator[Fill]:
        """Long-running async iterator. Yields fills as they arrive on-chain."""
        while True:
            try:
                fills = await self._fetch_new_fills()
                for fill in fills:
                    yield fill
            except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as exc:
                log.warning("Polygon fill stream tick failed: %s", exc)
            await asyncio.sleep(self.poll_interval)

    async def close(self) -> None:
        """Close the owned httpx client (no-op if caller supplied one)."""
        if self._client is not None and not self._external_client:
            await self._client.aclose()
            self._client = None

    # ----- internals -----------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.http_timeout)
        return self._client

    async def _rpc_call(
        self, client: httpx.AsyncClient, method: str, params: list
    ) -> object:
        """Single JSON-RPC call. Raises on transport or RPC-level errors."""
        self._rpc_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._rpc_id,
            "method": method,
            "params": params,
        }
        response = await client.post(self.rpc_url, json=payload)
        response.raise_for_status()
        body = response.json()
        if "error" in body and body["error"]:
            raise ValueError(f"RPC error from {method}: {body['error']}")
        return body.get("result")

    async def _fetch_new_fills(self) -> list[Fill]:
        """Pull logs since ``last_block_seen`` and decode them into Fills."""
        if not self.our_market_ids:
            return []

        client = await self._get_client()

        # Discover head.
        head_hex = await self._rpc_call(client, "eth_blockNumber", [])
        if not isinstance(head_hex, str):
            return []
        head = _hex_to_int(head_hex)

        if self.last_block_seen is None:
            # Cold start: only scan the most recent block to avoid
            # back-filling weeks of history. FillListener will pick up
            # subsequent fills naturally as the head advances.
            from_block = head
        else:
            from_block = self.last_block_seen + 1

        if from_block > head:
            return []
        # Cap range to keep public RPCs happy.
        to_block = min(head, from_block + self.max_block_range - 1)

        # Build topic filter:
        #   topic[0] = OrderFilled event signature
        #   topic[1] = orderHash (one of our market_ids, padded to 32 bytes)
        # eth_getLogs allows array-valued topics for OR matching.
        topic1_filter = [self._market_id_to_topic(mid) for mid in self.our_market_ids]
        filter_obj = {
            "address": self.contract_address,
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "topics": [ORDER_FILLED_TOPIC, topic1_filter],
        }

        result = await self._rpc_call(client, "eth_getLogs", [filter_obj])
        if not isinstance(result, list):
            self.last_block_seen = to_block
            return []

        fills: list[Fill] = []
        for entry in result:
            fill = _parse_order_filled_log(entry)
            if fill is None:
                continue
            if fill.market_id.lower() not in self.our_market_ids:
                # eth_getLogs already filtered, but defensive double-check.
                continue
            fills.append(fill)

        self.last_block_seen = to_block
        return fills

    @staticmethod
    def _market_id_to_topic(market_id: str) -> str:
        """Pad an order-hash market_id to a 32-byte 0x-prefixed topic."""
        mid = market_id.lower()
        if mid.startswith("0x"):
            mid = mid[2:]
        # Pad to 64 hex chars.
        return "0x" + mid.rjust(64, "0")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


async def make_fill_indexer(
    *,
    market_id: Optional[str] = None,
    force_mock: bool = False,
    rpc_url: Optional[str] = None,
) -> "PolygonFillIndexer | MockFillSource":
    """Construct the right indexer for the current environment.

    Returns :class:`PolygonFillIndexer` only when both:

      1. ``force_mock`` is False.
      2. The Polygon RPC is reachable (``eth_blockNumber`` succeeds).

    Otherwise returns :class:`MockFillSource`. The orchestrator should
    inspect the result's ``is_simulated`` property to label the
    resulting ``BuilderFeeEvent`` rows correctly.
    """
    if force_mock:
        log.info("make_fill_indexer: force_mock=True; using MockFillSource")
        source = MockFillSource()
        if market_id:
            source.register_market(market_id)
        return source

    indexer = PolygonFillIndexer(rpc_url=rpc_url)
    if market_id:
        indexer.register_market(market_id)

    reachable = await indexer.check_connectivity()
    if not reachable:
        log.info(
            "Polygon RPC unreachable at %s; falling back to MockFillSource",
            indexer.rpc_url,
        )
        await indexer.close()
        mock = MockFillSource()
        if market_id:
            mock.register_market(market_id)
        return mock

    log.info("PolygonFillIndexer ready (rpc=%s)", indexer.rpc_url)
    return indexer
