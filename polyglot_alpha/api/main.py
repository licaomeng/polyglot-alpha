"""FastAPI application entrypoint for PolyglotAlpha v2."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler

from ..persistence import init_db
from ..pubsub import get_pubsub
from .rate_limit import limiter
from .routes import (
    agents,
    builder_fees,
    events,
    leaderboard,
    polymarket,
    sse,
    trigger,
)

logger = logging.getLogger(__name__)


# Safe default origins for local development. Production deployments must
# override via the ``CORS_ORIGINS`` env var (comma-separated list).
DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
)

ALLOWED_METHODS: tuple[str, ...] = ("GET", "POST", "OPTIONS")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan hook: create tables + warm pub/sub singleton."""

    logger.info("polyglot_alpha: starting up; initializing DB")
    init_db()
    get_pubsub()
    try:
        yield
    finally:
        logger.info("polyglot_alpha: shutting down")


def _build_cors_origins() -> list[str]:
    """Parse ``CORS_ORIGINS`` env var into a list of safe origins.

    Wildcard ``*`` is incompatible with ``allow_credentials=True``
    (FastAPI/Starlette will silently drop credentials). If a caller
    sets ``CORS_ORIGINS="*"`` we fall back to the safe defaults and log
    a warning so the misconfiguration is visible.
    """

    raw = os.environ.get("CORS_ORIGINS")
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    parts = [o.strip() for o in raw.split(",") if o.strip()]
    if any(p == "*" for p in parts):
        logger.warning(
            "CORS_ORIGINS contains '*' which is incompatible with "
            "allow_credentials=True; falling back to safe defaults"
        )
        return list(DEFAULT_CORS_ORIGINS)
    return parts


def create_app() -> FastAPI:
    app = FastAPI(
        title="PolyglotAlpha v2 API",
        version="0.2.0",
        lifespan=lifespan,
    )

    # ----- Rate limiting (slowapi) -----
    # Register the limiter on the app state, install the middleware, and
    # wire the 429 handler so RateLimitExceeded responses are returned
    # automatically.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # ----- CORS (hardened) -----
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_build_cors_origins(),
        allow_credentials=True,
        allow_methods=list(ALLOWED_METHODS),
        allow_headers=["*"],
    )

    app.include_router(events.router)
    app.include_router(agents.router)
    app.include_router(leaderboard.router)
    app.include_router(builder_fees.router)
    app.include_router(sse.router)
    app.include_router(trigger.router)
    app.include_router(polymarket.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        return {"name": "polyglot-alpha", "version": app.version}

    return app


app = create_app()
