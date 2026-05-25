"""Synthetic Polymarket client used in demos and tests.

Goal: behaviorally indistinguishable from the real client from the
orchestrator's point of view, but never touches the network. Useful in
three situations:

  1. Hackathon demo when we don't want to actually submit markets.
  2. CI where outbound network is blocked.
  3. Local dev before the operator has Polymarket API credentials.

The fill stream is a Poisson process with mean 5 fills/minute/market
and per-fill notionals drawn uniformly from ``$50..$500``. Builder fees
are computed at the documented 0.4% rate.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
import uuid
from typing import Optional

from polyglot_alpha.polymarket.types import Fill, Question, SubmissionResult

# Polymarket V2 builder fee — 0.4% of fill notional.
BUILDER_FEE_RATE = 0.004

# Mean fills per minute per market for the synthetic stream.
DEFAULT_FILLS_PER_MINUTE = 5.0


class MockPolymarketClient:
    """In-memory Polymarket simulator.

    Each ``submit_question`` call mints a UUID market id; ``list_fills``
    rolls forward a Poisson process from the market's creation time
    (or last polled timestamp) and returns whatever events landed in
    that window. The simulator is fully deterministic when ``seed`` is
    provided.
    """

    def __init__(
        self,
        builder_code: str,
        *,
        seed: Optional[int] = None,
        fills_per_minute: float = DEFAULT_FILLS_PER_MINUTE,
        time_fn=time.time,
    ) -> None:
        self.builder_code = builder_code
        self._rng = random.Random(seed)
        self._fills_per_minute = fills_per_minute
        self._time_fn = time_fn
        # market_id -> creation timestamp (so we know how much synthetic
        # time has elapsed since the market opened).
        self._markets: dict[str, dict] = {}

    @property
    def is_simulated(self) -> bool:
        return True

    async def submit_question(self, question: Question) -> SubmissionResult:
        # Yield once so callers can await us in a tight loop without
        # starving the event loop — keeps semantic parity with the real
        # async client.
        await asyncio.sleep(0)
        market_id = f"mock-{uuid.uuid4().hex[:12]}"
        now = int(self._time_fn())
        self._markets[market_id] = {
            "question": question.model_dump(),
            "created_at": now,
            "status": "open",
        }
        return SubmissionResult(
            market_id=market_id,
            polymarket_url=f"https://polymarket.com/market/{market_id}",
            status="submitted",
            fees_estimate_usdc=question.initial_liquidity_usdc * BUILDER_FEE_RATE,
            is_simulated=True,
        )

    async def get_market_status(self, market_id: str) -> dict:
        await asyncio.sleep(0)
        market = self._markets.get(market_id)
        if market is None:
            return {"market_id": market_id, "status": "unknown", "is_simulated": True}
        return {
            "market_id": market_id,
            "status": market["status"],
            "created_at": market["created_at"],
            "is_simulated": True,
        }

    async def list_fills(self, market_id: str, since_ts: int) -> list[Fill]:
        """Return any synthetic fills with ``timestamp > since_ts``.

        We sample a fresh batch on every call: a Poisson-distributed
        count over the window ``(since_ts, now]`` with each fill placed
        uniformly inside that window. This is good enough for a demo —
        FillListener polls every 30s and we want to see ~2 fills per
        poll for a market running at the default rate.
        """
        await asyncio.sleep(0)
        if market_id not in self._markets:
            return []

        now = int(self._time_fn())
        if since_ts >= now:
            return []

        elapsed_seconds = now - since_ts
        elapsed_minutes = elapsed_seconds / 60.0
        expected = self._fills_per_minute * elapsed_minutes
        # Cap the lambda so an unrealistically large window doesn't
        # produce a million fills in tests.
        expected = min(expected, 1000.0)
        n_fills = _poisson(self._rng, expected)

        fills: list[Fill] = []
        for _ in range(n_fills):
            ts_offset = self._rng.randint(1, max(1, elapsed_seconds))
            fill_amount = round(self._rng.uniform(50.0, 500.0), 2)
            fills.append(
                Fill(
                    fill_id=f"mockfill-{uuid.uuid4().hex[:16]}",
                    market_id=market_id,
                    fill_amount_usdc=fill_amount,
                    builder_fee_usdc=round(fill_amount * BUILDER_FEE_RATE, 6),
                    timestamp=since_ts + ts_offset,
                    taker_address=f"0x{self._rng.randrange(16**40):040x}",
                    is_simulated=True,
                )
            )
        fills.sort(key=lambda f: f.timestamp)
        return fills

    async def close(self) -> None:
        """No-op for API parity with the real client."""
        return None


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's algorithm — fine for the small lambdas we generate."""
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1
