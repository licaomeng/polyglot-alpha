"""Singleton ``slowapi`` rate limiter used by the API routes.

Centralising the :class:`Limiter` here so each route module can import
the same instance is required by ``slowapi`` — the limiter must also be
registered on the FastAPI ``app`` via
``app.state.limiter = limiter``.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address


# Global default: 100 requests/minute per remote address. Expensive
# endpoints (LLM-backed ``/trigger/event``, long-lived ``/sse/events``)
# layer their own tighter limits via ``@limiter.limit("10/minute")``.
DEFAULT_RATE: str = "100/minute"


limiter: Limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[DEFAULT_RATE],
)


__all__ = ["limiter", "DEFAULT_RATE"]
