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


# Auction-only event types — see polyglot_alpha.orchestrator publishes.
_AUCTION_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "auction.opened",
        "auction.bid_submitted",
        "auction.settled",
        "auction.closed",
        "bid.submitted",
        "bid.accepted",
    }
)


async def _auction_event_iter(request: Request) -> AsyncIterator[dict[str, str]]:
    """Wildcard auction stream: yields only auction.* events to subscribers.

    External operators connect to this stream to watch every open auction
    on Arc without needing to know event IDs in advance. The filter is
    applied server-side so the client can stay dumb.
    """

    hub = get_hub()
    async with hub.subscribe() as queue:
        yield {
            "event": "hello",
            "data": json.dumps(
                {
                    "stream": "auctions",
                    "subscribers": hub.subscriber_count,
                }
            ),
        }
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=HEARTBEAT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                yield {"event": "heartbeat", "data": "{}"}
                continue
            ev_type = event.get("type", "message")
            # Forward auction.* events and bid.* events; drop everything else.
            if ev_type not in _AUCTION_EVENT_TYPES and not ev_type.startswith("auction."):
                continue
            yield {
                "event": ev_type,
                "data": json.dumps(event.get("data", {}), default=str),
            }


@router.get(
    "/auctions",
    summary="Server-Sent Events stream of every open/settled auction",
)
@limiter.limit("10/minute")
async def sse_auctions(request: Request) -> EventSourceResponse:
    """Wildcard auction stream for external operators.

    Subscribe with::

        curl http://localhost:8000/sse/auctions

    Emits one event per ``auction.opened`` / ``auction.bid_submitted`` /
    ``auction.settled`` lifecycle transition. Use the ``event_id`` in the
    payload to POST a bid back to ``/api/auctions/{event_id}/bid``.
    """

    return EventSourceResponse(_auction_event_iter(request))
