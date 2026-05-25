"""SSE endpoint streaming orchestrator lifecycle events."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from ..deps import get_hub
from ..rate_limit import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sse", tags=["sse"])

HEARTBEAT_INTERVAL_SECONDS: float = 15.0


async def _event_iter(request: Request) -> AsyncIterator[dict[str, str]]:
    hub = get_hub()
    async with hub.subscribe() as queue:
        # Emit an initial hello so clients know they're connected.
        yield {
            "event": "hello",
            "data": json.dumps({"subscribers": hub.subscriber_count}),
        }
        while True:
            if await request.is_disconnected():
                logger.debug("sse: client disconnected")
                return
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=HEARTBEAT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                yield {"event": "heartbeat", "data": "{}"}
                continue
            yield {
                "event": event.get("type", "message"),
                "data": json.dumps(event.get("data", {}), default=str),
            }


@router.get("/events", summary="Server-Sent Events stream of lifecycle events")
@limiter.limit("10/minute")
async def sse_events(request: Request) -> EventSourceResponse:
    return EventSourceResponse(_event_iter(request))
