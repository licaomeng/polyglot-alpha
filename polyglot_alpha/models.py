"""Central registry of LLM model IDs.

Source-of-truth model snapshots are pinned here and every per-role
assignment defaults to one of them. All values are overrideable via
environment variables so operators can swap models per environment
(staging vs. prod, A/B trials, future provider migrations) without
touching any source.

Open-source principle: provider + model version + API key all live in
``.env`` — never hard-coded in code. To swap providers (e.g. to OpenAI),
write a new factory in :mod:`polyglot_alpha.llm` and override these
``MODEL_*`` variables in ``.env``; no further code changes required.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Pinned default snapshots (Anthropic Claude 4.5 family)
# ---------------------------------------------------------------------------

CLAUDE_HAIKU: str = os.environ.get("MODEL_HAIKU", "claude-haiku-4-5-20251001")
CLAUDE_SONNET: str = os.environ.get("MODEL_SONNET", "claude-sonnet-4-5-20250929")

# ---------------------------------------------------------------------------
# Per-role assignments. Each defaults to one of the snapshots above but can
# be pinned independently from the environment for surgical overrides.
# ---------------------------------------------------------------------------

MODEL_TRANSLATOR: str = os.environ.get("MODEL_TRANSLATOR", CLAUDE_HAIKU)
MODEL_CRITIC: str = os.environ.get("MODEL_CRITIC", CLAUDE_HAIKU)
MODEL_MODERATOR: str = os.environ.get("MODEL_MODERATOR", CLAUDE_SONNET)
MODEL_REFINE: str = os.environ.get("MODEL_REFINE", CLAUDE_HAIKU)
MODEL_SYNTHESIZER: str = os.environ.get("MODEL_SYNTHESIZER", CLAUDE_HAIKU)
MODEL_MQM_JUDGE: str = os.environ.get("MODEL_MQM_JUDGE", CLAUDE_HAIKU)
MODEL_STYLE_JUDGE: str = os.environ.get("MODEL_STYLE_JUDGE", CLAUDE_HAIKU)
MODEL_NEWS_SCORER: str = os.environ.get("MODEL_NEWS_SCORER", CLAUDE_HAIKU)

# ---------------------------------------------------------------------------
# Provider-label helper: judges record "<provider>:<model>" in their
# evidence JSON so downstream cost / audit logs can group by snapshot.
# ---------------------------------------------------------------------------

ANTHROPIC_PROVIDER_LABEL: str = f"anthropic:{CLAUDE_HAIKU}"


__all__ = [
    "CLAUDE_HAIKU",
    "CLAUDE_SONNET",
    "MODEL_TRANSLATOR",
    "MODEL_CRITIC",
    "MODEL_MODERATOR",
    "MODEL_REFINE",
    "MODEL_SYNTHESIZER",
    "MODEL_MQM_JUDGE",
    "MODEL_STYLE_JUDGE",
    "MODEL_NEWS_SCORER",
    "ANTHROPIC_PROVIDER_LABEL",
]
