"""In-memory async pub/sub with optional Redis fan-out.

The orchestrator + API publish lifecycle events here; the SSE endpoint
subscribes. If ``REDIS_URL`` is set we mirror published payloads to Redis
``PUBLISH`` so multiple FastAPI workers can share a stream, but local
subscribers always read from the in-memory queue (single-worker mode is the
default for the hackathon demo).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

REDIS_URL: str | None = os.environ.get("REDIS_URL") or None
REDIS_CHANNEL: str = os.environ.get("REDIS_CHANNEL", "polyglot_alpha.events")


class PubSub:
    """In-memory fan-out hub. Thread-safe via the asyncio event loop."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self._redis: Any | None = None

    async def _ensure_redis(self) -> None:
        if not REDIS_URL or self._redis is not None:
            return
        try:
            import redis.asyncio as redis  # type: ignore

            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
            logger.info("pubsub: connected to redis at %s", REDIS_URL)
        except Exception as exc:  # pragma: no cover - optional dep
            logger.warning("pubsub: redis unavailable (%s); in-memory only", exc)
            self._redis = None

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Broadcast a typed payload to every subscriber."""

        message: dict[str, Any] = {
            "type": event_type,
            "data": payload,
        }
        async with self._lock:
            queues = list(self._subscribers)
        for queue in queues:
            # Don't block publishers on slow consumers; drop if full.
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("pubsub: dropping event for full subscriber")
        await self._ensure_redis()
        if self._redis is not None:  # pragma: no cover - requires redis
            try:
                await self._redis.publish(REDIS_CHANNEL, json.dumps(message))
            except Exception as exc:
                logger.warning("pubsub: redis publish failed: %s", exc)

    @asynccontextmanager
    async def subscribe(
        self, maxsize: int = 256
    ) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def stream(
        self, maxsize: int = 256
    ) -> AsyncIterator[dict[str, Any]]:
        """Convenience async iterator over events."""

        async with self.subscribe(maxsize=maxsize) as queue:
            while True:
                event = await queue.get()
                yield event

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


_HUB: PubSub | None = None


def get_pubsub() -> PubSub:
    """Singleton accessor."""

    global _HUB
    if _HUB is None:
        _HUB = PubSub()
    return _HUB


def reset_pubsub() -> None:
    """Test helper: drop the singleton."""

    global _HUB
    _HUB = None


__all__ = ["PubSub", "get_pubsub", "reset_pubsub", "REDIS_URL"]
