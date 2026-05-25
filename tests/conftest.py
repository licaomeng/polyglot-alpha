"""Shared pytest fixtures (ingestion + backend tests)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

# Ensure the repo root is on sys.path so ``polyglot_alpha`` is importable when
# running ``pytest`` from anywhere in the tree.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Isolated SQLite DB per test (function scope).
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_db(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Point the persistence engine at a fresh on-disk sqlite file.

    Using on-disk SQLite (instead of ``:memory:``) keeps the engine usable
    across multiple sessions opened by the orchestrator's
    ``session_scope()`` context manager.
    """

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        url = f"sqlite:///{db_path}"
        monkeypatch.setenv("DATABASE_URL", url)

        # Rebuild engine + create tables.
        from polyglot_alpha.persistence import db as persistence_db
        from polyglot_alpha.persistence import init_db

        persistence_db.reset_engine(url)
        init_db()

        # Reset pub/sub so each test starts clean.
        from polyglot_alpha import pubsub as pubsub_mod

        pubsub_mod.reset_pubsub()

        # Reset the slowapi limiter so per-IP buckets do not leak across
        # tests (the TestClient always uses ``testclient`` as the remote
        # address, so the per-route limits would otherwise accumulate).
        try:
            from polyglot_alpha.api.rate_limit import limiter as _limiter

            _limiter.reset()
        except Exception:
            pass

        yield url

        # Tear-down: drop the singleton so the next test rebuilds.
        pubsub_mod.reset_pubsub()
        try:
            from polyglot_alpha.api.rate_limit import limiter as _limiter

            _limiter.reset()
        except Exception:
            pass


@pytest.fixture()
def sample_event() -> dict[str, Any]:
    return {
        "title": "Sample geopolitical event for tests",
        "sources": [
            {"name": "test-source", "url": "https://example.com/a"},
        ],
        "language": "en",
        "category": "geopolitics",
    }
