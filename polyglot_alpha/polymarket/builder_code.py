"""Builder-code registration & resolution.

Polymarket V2 (May 2026) lets builders register a short alphanumeric
code at ``polymarket.com/settings?tab=builder`` and earns 0.4% of every
fill routed through that code. For the hackathon we cannot reliably
reach the production registration UI from a headless backend, so we
keep two modes:

* **real** — operator pastes the code returned by the Polymarket UI
  into the environment / DB and we just thread it through.
* **demo** — we deterministically derive a 10-char code from the
  translator's wallet address. The same address always maps to the
  same code, which lets ``resolve_translator_for_code`` reverse the
  lookup without external state.

The mapping ``code -> translator_address`` is what
``BuilderFeeRouter.recordFill`` needs at fill time; we keep an
in-process cache plus a tiny JSON file under ``outputs/`` so the
mapping survives restarts during demos.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Optional

BUILDER_CODE_LENGTH = 10

# Path to the on-disk demo registry. Kept under outputs/ so it sits with
# the other hackathon artifacts and is easy to clean between demos.
_DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "outputs" / "builder_codes.json"
)

# Guard concurrent writes from FillListener tasks running in parallel.
_REGISTRY_LOCK = threading.Lock()


def _registry_path() -> Path:
    """Allow tests to override the on-disk path via env var."""
    override = os.getenv("POLYGLOT_BUILDER_REGISTRY_PATH")
    return Path(override) if override else _DEFAULT_REGISTRY_PATH


def _load_registry() -> dict[str, str]:
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        # Normalize: code -> address (lowercased hex). Drop anything malformed.
        return {
            str(code): str(addr).lower()
            for code, addr in data.items()
            if isinstance(code, str) and isinstance(addr, str)
        }
    except (OSError, json.JSONDecodeError):
        return {}


def _save_registry(registry: dict[str, str]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(registry, fh, indent=2, sort_keys=True)
    tmp.replace(path)


def _derive_demo_code(translator_address: str) -> str:
    """Deterministic ``BUILDER_CODE_LENGTH``-char code from an address.

    Uses SHA-256 truncated to keep collisions cosmetic for a hackathon
    (probability ~ 2^-40 across realistic demo populations).
    """
    digest = hashlib.sha256(translator_address.lower().encode("utf-8")).hexdigest()
    return digest[:BUILDER_CODE_LENGTH].upper()


def register_builder_code(
    translator_address: str,
    *,
    real_code: Optional[str] = None,
) -> str:
    """Register a builder code for ``translator_address``.

    Parameters
    ----------
    translator_address:
        EVM address of the translator agent. Required.
    real_code:
        If provided, the caller already obtained a code from
        polymarket.com (real mode). We persist the mapping but do not
        derive anything. In demo mode this is ``None`` and we derive
        deterministically.

    Returns
    -------
    The builder code (alphanumeric, ``BUILDER_CODE_LENGTH`` chars in
    demo mode; arbitrary length in real mode).
    """
    if not translator_address or not translator_address.startswith("0x"):
        raise ValueError("translator_address must be a 0x-prefixed EVM address")

    code = real_code.strip() if real_code else _derive_demo_code(translator_address)
    if not code:
        raise ValueError("builder code must be non-empty")

    with _REGISTRY_LOCK:
        registry = _load_registry()
        registry[code] = translator_address.lower()
        _save_registry(registry)
    return code


def resolve_translator_for_code(code: str) -> Optional[str]:
    """Reverse the mapping. Returns ``None`` if the code is unknown."""
    if not code:
        return None
    with _REGISTRY_LOCK:
        registry = _load_registry()
    return registry.get(code)
