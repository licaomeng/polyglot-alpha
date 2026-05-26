"""FastAPI application entrypoint for PolyglotAlpha v2."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler
from sqlmodel import select

from ..llm import shutdown_anthropic
from ..logging_ctx import install_event_id_filter
from ..persistence import init_db, session_scope
from ..persistence.models import Event, EventStatus
from ..pubsub import get_pubsub
from .rate_limit import limiter
from .routes import (
    agents,
    builder_fees,
    events,
    leaderboard,
    operators,
    polymarket,
    sse,
    trigger,
)

logger = logging.getLogger(__name__)


# Non-terminal lifecycle statuses. If an event row is still in any of these
# states on backend startup and is older than the recovery cutoff, the
# previous backend process almost certainly crashed mid-lifecycle and the
# in-memory orchestrator task is gone — sweep these rows to FAILED so the
# UI doesn't display perpetual "running" badges.
_NON_TERMINAL_STATUSES: tuple[str, ...] = (
    EventStatus.PENDING.value,
    EventStatus.AUCTION_OPEN.value,
    EventStatus.AUCTION_SETTLED.value,
    EventStatus.TRANSLATING.value,
    EventStatus.EVALUATING.value,
)


def _sweep_stuck_events() -> int:
    """Mark crashed-in-flight events as FAILED on startup.

    A previous backend process that crashed (OOM, restart, panic) cannot
    finish the lifecycle task it owned. Any row still in a non-terminal
    status that is older than ``2 * AUCTION_WINDOW_SECONDS +
    PANEL_TIMEOUT_SECONDS`` is past every legitimate phase budget and must
    be flipped to ``FAILED`` so /events views don't show a stuck row.

    Returns the number of rows updated. Best-effort: any exception is
    logged and swallowed so a sweep failure cannot block app startup.
    """

    try:
        auction_s = float(os.environ.get("AUCTION_WINDOW_SECONDS", "60"))
        panel_s = float(os.environ.get("PANEL_TIMEOUT_SECONDS", "120"))
        # 2 * auction (open + settle drift) + panel + a small slack so we
        # never race a still-healthy lifecycle that's near its tail.
        cutoff_seconds = 2.0 * auction_s + panel_s
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=cutoff_seconds)

        swept = 0
        with session_scope() as session:
            stmt = select(Event).where(
                Event.status.in_(_NON_TERMINAL_STATUSES),  # type: ignore[attr-defined]
                Event.triggered_at < cutoff,
            )
            for row in session.exec(stmt).all():
                row.status = EventStatus.FAILED.value
                session.add(row)
                swept += 1
        if swept:
            logger.warning(
                "startup_recovery: swept %d stuck event(s) to FAILED "
                "(cutoff=%.0fs, reason=startup_recovery)",
                swept,
                cutoff_seconds,
            )
        else:
            logger.info("startup_recovery: no stuck events found")
        return swept
    except Exception:  # noqa: BLE001 — best-effort, must not block startup
        logger.exception("startup_recovery sweep failed; continuing startup")
        return 0


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
    """Lifespan hook: create tables + warm pub/sub singleton.

    On shutdown we explicitly ``aclose()`` the shared ``AsyncAnthropic``
    client so its underlying ``httpx.AsyncClient`` is closed *while* the
    event loop is still alive. Without this the SDK client gets
    finalized post-loop-close and we see ``RuntimeError: Event loop is
    closed`` tracebacks in the backend log on every shutdown.
    """

    logger.info("polyglot_alpha: starting up; initializing DB")
    # Install the [event_id=N] correlation-id filter on the root logger so
    # every subsystem's log line carries the active lifecycle id (see
    # polyglot_alpha.logging_ctx). No-op if already installed.
    install_event_id_filter()
    init_db()
    # Recover any events left in non-terminal states by a previously
    # crashed/restarted backend process before warming pub/sub.
    _sweep_stuck_events()
    get_pubsub()
    try:
        yield
    finally:
        logger.info("polyglot_alpha: shutting down")
        await shutdown_anthropic()


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
    app.include_router(operators.router)
    app.include_router(operators.bid_router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        return {"name": "polyglot-alpha", "version": app.version}

    return app


app = create_app()
