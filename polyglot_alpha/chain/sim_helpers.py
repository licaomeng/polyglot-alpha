"""Synthetic chain-result helpers for ``mode=mock`` lifecycles.

W5-A2 — when the event was triggered with ``mode='mock'`` we MUST NOT make
any real Arc / web3 RPC calls. Every chain-touching subroutine reads
:func:`is_mock_mode` and, if true, returns a synthetic result built from
the helpers in this module.

The UI (W2-3) gates arcscan links on the ``0xsim_`` prefix and renders
muted text instead of a clickable explorer URL — so the prefix is part
of a public contract between backend and frontend. Do not change it
without coordinating UI updates.

Access pattern:

    from polyglot_alpha.chain.sim_helpers import (
        event_mode_ctx, is_mock_mode, sim_tx_hash, sim_ipfs_hash,
    )

    # W5-A1 sets the contextvar when the lifecycle starts; the chain
    # subroutines just call ``is_mock_mode()``.
    if is_mock_mode():
        return {"tx_hash": sim_tx_hash(), ...}

The contextvar approach was chosen over an explicit ``mode`` parameter
because the chain modules already have ~6 separate call sites and the
parameter would have to be threaded through agents, the auction client,
and the builder-fee router. The contextvar keeps the blast radius small.
"""

from __future__ import annotations

import hashlib
import secrets
from contextvars import ContextVar
from typing import Optional

# ---------------------------------------------------------------------------
# Sentinel constants
# ---------------------------------------------------------------------------

# Prefix every synthetic tx hash. The UI muted-text gate compares case-
# insensitively, so callers can safely use the lower-case form everywhere.
SIM_TX_HASH_PREFIX: str = "0xsim_"

# Prefix for synthetic IPFS pointers (anchor pipeline traces). Mirrors the
# convention used by the IPFS fallback path in W2-3 so the UI's existing
# "ipfs://sim/..." muted-text gate keeps working.
SIM_IPFS_PREFIX: str = "ipfs://sim/"


# ---------------------------------------------------------------------------
# Mode contextvar (set by W5-A1 plumbing once the lifecycle starts)
# ---------------------------------------------------------------------------

event_mode_ctx: ContextVar[Optional[str]] = ContextVar(
    "polyglot_alpha_event_mode", default=None
)


def set_event_mode(mode: Optional[str]) -> object:
    """Bind ``mode`` for the current async context.

    Returns the contextvar token so the caller can ``reset`` it on exit.
    Pass ``None`` to clear.
    """

    return event_mode_ctx.set(mode)


def reset_event_mode(token: object) -> None:
    """Restore the previous mode value bound to ``token``."""

    event_mode_ctx.reset(token)  # type: ignore[arg-type]


def is_mock_mode(explicit_mode: Optional[str] = None) -> bool:
    """Return ``True`` when chain calls should produce synthetic results.

    Resolution order:
      1. ``explicit_mode`` argument (caller-supplied; e.g. an
         orchestrator subroutine that already received an ``auction_mode``
         parameter from the public lifecycle entrypoint).
      2. :data:`event_mode_ctx` contextvar — local to this module; used
         as a fallback if W5-A1's :mod:`logging_ctx` is not importable.
      3. :func:`polyglot_alpha.logging_ctx.get_event_mode` — the canonical
         W5-A1 plumbing read here so we honour the single source of truth.
      4. ``False`` (default to real chain calls).

    Comparison is case-insensitive against the literal ``"mock"`` so
    callers don't have to normalise. Any non-``"mock"`` value falls
    through to the chain.
    """

    if explicit_mode is not None:
        mode: Optional[str] = explicit_mode
    else:
        mode = event_mode_ctx.get()
        if mode is None:
            # Fall through to W5-A1's logging_ctx contextvar.
            try:
                from ..logging_ctx import get_event_mode as _get_event_mode
                mode = _get_event_mode()
            except ImportError:  # pragma: no cover - defensive
                mode = None
    if mode is None:
        return False
    return str(mode).strip().lower() == "mock"


# ---------------------------------------------------------------------------
# Synthetic-result generators
# ---------------------------------------------------------------------------


def sim_tx_hash() -> str:
    """Return a clearly-synthetic transaction hash.

    Shape: ``0xsim_<56 hex chars>``. Total length 62, distinct from a real
    66-char 0x-prefixed hash so any downstream length-based validator
    fails closed instead of treating it as real. The UI's link gate
    keys off the ``0xsim_`` prefix.
    """

    return f"{SIM_TX_HASH_PREFIX}{secrets.token_hex(28)}"


def sim_address() -> str:
    """Return a synthetic Ethereum-ish address for mock translators.

    Not used by the orchestrator today (mock bids carry real-looking
    deterministic addresses from the bid payload), but exposed for
    completeness in case downstream callers need to fabricate a recipient
    address without minting a real wallet.
    """

    return f"{SIM_TX_HASH_PREFIX}{secrets.token_hex(18)}"


def sim_ipfs_hash(content: Optional[str] = None) -> str:
    """Return a synthetic IPFS pointer.

    If ``content`` is provided the digest is deterministic so the same
    inputs always produce the same pointer (useful in tests). Otherwise
    a random 32-byte hex digest is used.
    """

    if content is None:
        digest = secrets.token_hex(32)
    else:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"{SIM_IPFS_PREFIX}{digest}"


# ---------------------------------------------------------------------------
# Detection helpers (used by API serializers + UI link-gating mirrors)
# ---------------------------------------------------------------------------


def is_sim_hash(value: Optional[str]) -> bool:
    """Return ``True`` when ``value`` is a synthetic ``0xsim_`` tx hash."""

    if not value or not isinstance(value, str):
        return False
    return value.lower().startswith(SIM_TX_HASH_PREFIX)


def is_sim_ipfs(value: Optional[str]) -> bool:
    """Return ``True`` when ``value`` is a synthetic ``ipfs://sim/`` pointer."""

    if not value or not isinstance(value, str):
        return False
    return value.lower().startswith(SIM_IPFS_PREFIX)


__all__ = [
    "SIM_TX_HASH_PREFIX",
    "SIM_IPFS_PREFIX",
    "event_mode_ctx",
    "set_event_mode",
    "reset_event_mode",
    "is_mock_mode",
    "sim_tx_hash",
    "sim_address",
    "sim_ipfs_hash",
    "is_sim_hash",
    "is_sim_ipfs",
]
