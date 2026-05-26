"""FastAPI application entrypoint for PolyglotAlpha v2."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler
from sqlmodel import func, select

from ..llm import shutdown_anthropic
from ..logging_ctx import install_event_id_filter
from ..persistence import engine as _persistence_engine, init_db, session_scope
from ..persistence.models import Event, EventStatus, FewShotExemplar
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


def _init_ingestion_tables() -> None:
    """Create the watcher-only ingestion tables on the persistence engine.

    ``polyglot_alpha.ingestion.models.RawEntry`` lives on a private SQLModel
    registry so it is **not** part of ``SQLModel.metadata`` and therefore
    not created by ``init_db()``. Without this hook the first RSS poll
    crashes with ``no such table: raw_entries``. Idempotent — running
    ``create_all`` against an already-present table is a no-op.
    """

    try:
        from ..ingestion.models import _INGESTION_METADATA

        _INGESTION_METADATA.create_all(_persistence_engine)
        logger.info("startup_recovery: ingestion metadata create_all completed")
    except Exception:  # noqa: BLE001 — best-effort, must not block startup
        logger.exception(
            "startup_recovery: ingestion metadata create_all failed; continuing"
        )


# When ``few_shot_exemplars`` is empty (fresh DB), auto-ingest the bundled
# ``EXTENDED_EXEMPLARS`` so the LLM judges have ICL examples available out of
# the box. Operators can disable this via ``SKIP_AUTO_INGEST_FEW_SHOTS=true``
# (useful for tests or for clusters that pre-seed via the one-shot script).
_AUTO_INGEST_FEW_SHOTS_ENV: str = "SKIP_AUTO_INGEST_FEW_SHOTS"


def _maybe_auto_ingest_few_shots() -> None:
    """Seed ``few_shot_exemplars`` from ``EXTENDED_EXEMPLARS`` if empty.

    Idempotent: only runs when the table count is zero. Any error is
    logged and swallowed so seeding cannot block startup.
    """

    if os.environ.get(_AUTO_INGEST_FEW_SHOTS_ENV, "").lower() in {"1", "true", "yes"}:
        logger.info(
            "startup_recovery: %s=true; skipping few-shot auto-ingest",
            _AUTO_INGEST_FEW_SHOTS_ENV,
        )
        return
    try:
        with session_scope() as session:
            existing = session.exec(
                select(func.count()).select_from(FewShotExemplar)
            ).one()
            # `existing` may be a tuple-like row depending on dialect.
            count = existing[0] if isinstance(existing, (tuple, list)) else int(existing)
        if count > 0:
            logger.info(
                "startup_recovery: few_shot_exemplars has %d row(s); skipping seed",
                count,
            )
            return
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception(
            "startup_recovery: failed to count few_shot_exemplars; skipping seed"
        )
        return

    try:
        from ..corpus.few_shots_extended import EXTENDED_EXEMPLARS
        from .._fewshots_seed import seed_few_shots_from_extended

        inserted = seed_few_shots_from_extended(EXTENDED_EXEMPLARS)
        logger.info(
            "startup_recovery: seeded few_shot_exemplars with %d row(s)", inserted
        )
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception(
            "startup_recovery: few-shot auto-ingest failed; continuing startup"
        )


def _truthy(value: str | None) -> bool:
    """Permissive truthy parser for env knobs (1/true/yes/on)."""

    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


async def _prewarm_d8_embedding_model() -> None:
    """Pre-load the SBert encoder used by D8 so the first event doesn't
    pay the cold-start tax (W3 measured ~60s on first FAISS+SBert hit).

    Runs once on startup as a non-blocking ``asyncio.create_task``: a
    failure here MUST NOT crash startup — D8 will just report
    INSUFFICIENT_DATA (W13-D) on the first event instead of silently
    passing. Disabled when ``D8_PREWARM`` is falsy (defaults to true) so
    test harnesses can skip the download.
    """

    if not _truthy(os.environ.get("D8_PREWARM", "true")):
        logger.info("d8.model_load: skipped (D8_PREWARM disabled)")
        return
    try:
        # Lazy import — keeps the SBert / FAISS deps out of test envs that
        # never start the FastAPI app.
        from polyglot_alpha.judges.style_alignment import d8_duplicate_detection

        t0 = time.perf_counter()
        model = await asyncio.to_thread(
            d8_duplicate_detection._load_embedding_model
        )
        elapsed = time.perf_counter() - t0
        if model is None:
            err = d8_duplicate_detection.get_last_model_load_error() or "unknown"
            logger.error(
                "d8.model_load: FAILED model=%s reason=%s "
                "(D8 will report INSUFFICIENT_DATA per W13-D)",
                d8_duplicate_detection.DEFAULT_MODEL_ID,
                err,
            )
            return
        logger.info(
            "d8.model_load: success model=%s elapsed=%.2fs",
            d8_duplicate_detection.DEFAULT_MODEL_ID,
            elapsed,
        )
    except Exception:  # noqa: BLE001 - must not block startup
        logger.exception(
            "d8.model_load: pre-warm crashed; D8 will lazy-load on first use"
        )


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
    # Create watcher-only ingestion tables (raw_entries) on the same DB
    # so the RSS aggregator can write dedup rows without crashing on first
    # poll. ``init_db()`` does not cover these because they live on a
    # private SQLModel registry — see ingestion/models.py for the
    # rationale.
    _init_ingestion_tables()
    # Recover any events left in non-terminal states by a previously
    # crashed/restarted backend process before warming pub/sub.
    _sweep_stuck_events()
    # Auto-seed FewShotExemplar from the bundled EXTENDED_EXEMPLARS when
    # the table is empty (fresh checkouts). Opt-out via
    # SKIP_AUTO_INGEST_FEW_SHOTS=true.
    _maybe_auto_ingest_few_shots()
    get_pubsub()
    # W13-D: pre-warm the SBert encoder used by D8 so the first event
    # doesn't pay the cold-load tax. Fire-and-forget via
    # ``asyncio.create_task`` so it never blocks lifespan startup; the
    # task logs its own outcome under ``d8.model_load:``.
    prewarm_task = asyncio.create_task(
        _prewarm_d8_embedding_model(), name="d8_prewarm"
    )
    try:
        yield
    finally:
        # Don't await the pre-warm task during shutdown — it's
        # fire-and-forget. Cancel only if still running so we don't
        # leak the worker thread holding the partial model load.
        if not prewarm_task.done():
            prewarm_task.cancel()
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
