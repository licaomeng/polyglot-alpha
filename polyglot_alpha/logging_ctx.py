"""Per-lifecycle correlation-ID logging context.

The pipeline crosses many subsystems (RSS -> news_summarizer -> trigger ->
orchestrator -> chain auction -> translator dispatch -> judges -> chain
question_registry -> polymarket -> builder_fee_router). Without a stable
correlation token it is nearly impossible to grep a single lifecycle's
log lines out of the interleaved backend log.

This module exposes a tiny :class:`contextvars.ContextVar` plus a
``logging.Filter`` that injects ``[event_id=N]`` into every record made
inside the same async task tree. Subsystems do not need to know about
the filter — they just keep calling ``logging.getLogger(__name__).info``
as usual, and the orchestrator (or any other entry point) calls
:func:`set_event_id` once after it allocates the lifecycle ID.

Usage
-----

    from polyglot_alpha.logging_ctx import install_event_id_filter, set_event_id

    install_event_id_filter()           # once, at process start
    set_event_id(42)                    # inside the lifecycle coroutine
    logging.getLogger(__name__).info("opening auction")
    # -> "[event_id=42] opening auction"
"""

from __future__ import annotations

import contextvars
import logging
from typing import Optional

# Per-async-context event id. ``contextvars.ContextVar`` propagates across
# ``asyncio`` task boundaries automatically, so a ``create_task`` spawned
# inside a lifecycle still sees the same id.
_current_event_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "event_id", default=None
)

# Per-async-context lifecycle mode (``"live"`` | ``"mock"``). Set once by
# the orchestrator after it adopts / creates the event row so any subsystem
# (translator dispatch, chain calls, news_summarizer, judge panel, fee
# router) can pick it up via :func:`get_event_mode` without us having to
# thread an explicit ``mode=`` argument through every helper signature.
_LIVE_EVENT_MODE: str = "live"
_current_event_mode: contextvars.ContextVar[str] = contextvars.ContextVar(
    "event_mode", default=_LIVE_EVENT_MODE
)


def set_event_id(event_id: Optional[int]) -> None:
    """Bind ``event_id`` to the current async context.

    Pass ``None`` to clear (e.g. at the end of a lifecycle, though the
    context-var implicitly resets when the coroutine exits).
    """

    _current_event_id.set(event_id)


def get_event_id() -> Optional[int]:
    """Return the current bound event id (or ``None``)."""

    return _current_event_id.get()


def set_event_mode(mode: Optional[str]) -> None:
    """Bind the lifecycle ``mode`` (``"live"`` | ``"mock"``) to this context.

    Falls back to ``"live"`` for any falsy / unknown value so callers
    can pass ``event.mode`` straight from the DB row without having to
    pre-normalize. Subsystems read this via :func:`get_event_mode`.
    """

    normalized = (mode or _LIVE_EVENT_MODE).strip().lower()
    if normalized not in ("live", "mock"):
        normalized = _LIVE_EVENT_MODE
    _current_event_mode.set(normalized)


def get_event_mode() -> str:
    """Return the current lifecycle mode (defaults to ``"live"``)."""

    return _current_event_mode.get() or _LIVE_EVENT_MODE


class EventIdFilter(logging.Filter):
    """``logging.Filter`` that injects ``event_id`` onto each :class:`LogRecord`.

    Adds two attributes:

    * ``record.event_id``  -> the raw int (or ``None``)
    * ``record.event_tag`` -> the human prefix ``"[event_id=N] "`` for
      formatters that don't know about the structured field.

    The formatter installed by :func:`install_event_id_filter` prepends
    ``event_tag`` so existing handlers Just Work.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        eid = _current_event_id.get()
        record.event_id = eid
        record.event_tag = f"[event_id={eid}] " if eid is not None else ""
        return True


# Sentinel used to make :func:`install_event_id_filter` idempotent — the
# filter and formatter wrap should only be attached once per process.
_FILTER_INSTALLED_ATTR = "_polyglot_event_id_filter_installed"


def install_event_id_filter(logger: Optional[logging.Logger] = None) -> None:
    """Attach :class:`EventIdFilter` to ``logger`` (root by default).

    The filter is also added to every existing handler so the
    ``event_id`` attribute survives any per-handler ``Filter.filter()``
    pipeline. We then wrap each handler's formatter to prepend
    ``event_tag`` to the rendered message — that way operators see the
    correlation id even without restructuring their handlers' format
    strings.

    Idempotent: a second call is a no-op so the filter is never doubled
    up on the same logger.
    """

    target = logger or logging.getLogger()
    if getattr(target, _FILTER_INSTALLED_ATTR, False):
        return

    flt = EventIdFilter()
    target.addFilter(flt)

    for handler in list(target.handlers):
        handler.addFilter(flt)
        original = handler.formatter or logging.Formatter("%(message)s")
        handler.setFormatter(_PrefixedFormatter(original))

    setattr(target, _FILTER_INSTALLED_ATTR, True)


class _PrefixedFormatter(logging.Formatter):
    """Delegate formatter that prepends ``event_tag`` to every line.

    Wrapping the existing formatter (rather than replacing it) preserves
    whatever format string operators have already configured (uvicorn,
    structlog adapters, etc.) and only adds the correlation prefix.
    """

    def __init__(self, wrapped: logging.Formatter) -> None:
        super().__init__()
        self._wrapped = wrapped

    def format(self, record: logging.LogRecord) -> str:
        rendered = self._wrapped.format(record)
        tag = getattr(record, "event_tag", "")
        if tag and not rendered.startswith(tag):
            return f"{tag}{rendered}"
        return rendered


__all__ = [
    "EventIdFilter",
    "get_event_id",
    "get_event_mode",
    "install_event_id_filter",
    "set_event_id",
    "set_event_mode",
]
