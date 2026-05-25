"""PolyglotAlpha v2 — top-level package."""

from __future__ import annotations

import os as _os
from pathlib import Path as _Path

__version__ = "0.2.0"


# Load .env early so submodules (chain/, agents/, polymarket/, ...) see the
# operator-configured contract addresses and API keys. The lookup walks up
# from this file's location so the import works regardless of CWD.
def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:  # pragma: no cover - dotenv ships in requirements
        return
    repo_root = _Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        # ``override=False`` means env vars set in the parent shell win,
        # so operators can still override per-process.
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_file()
del _load_env_file
