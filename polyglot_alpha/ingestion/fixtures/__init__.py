"""Canned news clusters used by ``event.mode == "mock"`` lifecycles.

The marketplace's regular flow polls live RSS sources and asks Claude
Haiku to score the freshest cluster. When the operator triggers an event
in ``mock`` mode (the demo button's deterministic path) we instead pick
one of the 5 canned multi-language clusters bundled in this directory
so the lifecycle completes in <10s with zero network calls.

Each ``news_cluster_<lang>.json`` matches the dict shape returned by
:func:`polyglot_alpha.api.routes.trigger._fetch_rss_demo_event`:

    {title, sources, language, category, summary, scoring}

where ``scoring`` is a JSON projection of
:class:`polyglot_alpha.ingestion.news_summarizer.EventScoring`.

The fixtures are intentionally realistic (real-sounding institutions,
plausible numbers, valid resolution semantics) so the 11-judge panel
exercises its style / framing / source / duplicate checks against
content of comparable shape to live news — not against ``"Test 123"``.
"""

from .loader import (
    FIXTURES_DIR,
    available_languages,
    fixture_paths,
    load_fixture,
    pick_mock_cluster,
)

__all__ = [
    "FIXTURES_DIR",
    "available_languages",
    "fixture_paths",
    "load_fixture",
    "pick_mock_cluster",
]
