"""Loader for canned mock-mode news clusters.

Exposes a thin helper :func:`pick_mock_cluster` consumed by
:func:`polyglot_alpha.api.routes.trigger._fetch_rss_demo_event` (and any
other mock-mode entry point) to short-circuit the RSS poll + Haiku
scoring pipeline with a realistic offline cluster.

Determinism
-----------
The picker uses :func:`random.choice` by default so successive demo
clicks rotate across the 5 language fixtures. Tests that need a stable
choice can pass a seeded :class:`random.Random` instance via the ``rng``
argument, or pick by language with :func:`load_fixture`.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Directory that holds the ``news_cluster_<lang>.json`` files. Anchored
# against this module's path so the fixtures travel with the package
# regardless of the caller's CWD.
FIXTURES_DIR: Path = Path(__file__).resolve().parent

# Filename glob pattern. Anything in this directory that doesn't match
# the pattern (README, this loader, ``__init__.py``) is ignored.
_FIXTURE_GLOB: str = "news_cluster_*.json"


def fixture_paths() -> list[Path]:
    """Return every ``news_cluster_<lang>.json`` file in the fixtures dir,
    sorted by name so the order is deterministic across platforms.
    """

    return sorted(FIXTURES_DIR.glob(_FIXTURE_GLOB))


def available_languages() -> list[str]:
    """Return the language codes for which a fixture is bundled.

    Parsed off the filename stem (``news_cluster_zh.json`` -> ``"zh"``).
    """

    out: list[str] = []
    for path in fixture_paths():
        # ``news_cluster_zh`` -> ``["news", "cluster", "zh"]``
        parts = path.stem.split("_")
        if len(parts) >= 3:
            out.append(parts[-1])
    return out


def _read_cluster_json(path: Path) -> dict[str, Any]:
    """Read a fixture file and return its parsed JSON payload.

    Raises :class:`FileNotFoundError` or :class:`json.JSONDecodeError`
    unchanged so the caller can decide whether to surface the failure or
    fall back. The wrapper here only adds logging.
    """

    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_fixture(language: str) -> dict[str, Any]:
    """Load the fixture for a specific language code (e.g. ``"zh"``).

    Raises :class:`FileNotFoundError` when the requested language has
    no bundled fixture.
    """

    target = FIXTURES_DIR / f"news_cluster_{language}.json"
    if not target.is_file():
        raise FileNotFoundError(
            f"No mock news fixture for language={language!r}; "
            f"available: {available_languages()}"
        )
    return _read_cluster_json(target)


def pick_mock_cluster(
    *,
    rng: Optional[random.Random] = None,
    language: Optional[str] = None,
) -> dict[str, Any]:
    """Return one canned news cluster as a plain dict.

    Args:
        rng: Optional :class:`random.Random` for deterministic selection
            (e.g. seeded in tests). Defaults to the module-level RNG.
        language: When provided, short-circuits the random choice and
            returns the fixture for that language. Raises if missing.

    The returned dict matches the shape expected by ``run_lifecycle``'s
    ``event_dict`` argument (``title``, ``sources``, ``language``,
    ``category``, ``summary``, ``scoring``).
    """

    if language is not None:
        cluster = load_fixture(language)
        logger.info(
            "fixtures.pick_mock_cluster: language=%s title=%r",
            language,
            (cluster.get("title") or "")[:80],
        )
        return cluster

    paths = fixture_paths()
    if not paths:
        raise RuntimeError(
            f"No mock news fixtures found in {FIXTURES_DIR}; "
            "expected at least one news_cluster_<lang>.json file."
        )
    picker = rng if rng is not None else random
    chosen = picker.choice(paths)
    cluster = _read_cluster_json(chosen)
    logger.info(
        "fixtures.pick_mock_cluster: chose=%s title=%r",
        chosen.name,
        (cluster.get("title") or "")[:80],
    )
    return cluster


__all__ = [
    "FIXTURES_DIR",
    "available_languages",
    "fixture_paths",
    "load_fixture",
    "pick_mock_cluster",
]
