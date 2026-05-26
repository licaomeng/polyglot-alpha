"""``SeederBeta`` — geopolitics-specialist reference seeder.

Specialty: geopolitics, diplomacy, sanctions, trade wars, elections.
System prompt emphasises multi-source verification and prefers official
government / state-media outlets as the resolution source. Temperature
sits in the middle (0.5) to balance precision with the wording
flexibility needed for political phrasing.

Wallet slot stays as ``"deepseek"`` for the same reason
:class:`SeederAlpha` keeps the ``"gemini"`` slot: deterministic wallet
derivation + historical on-chain records must remain stable.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..llm import (
    CLAUDE_HAIKU,
    DEFAULT_MAX_TOKENS,
    make_llm,
)
from .base import BaseTranslatorAgent

# ---------------------------------------------------------------------------
# Persona prompt
# ---------------------------------------------------------------------------

_SEEDER_BETA_SYSTEM = (
    "You are Seeder Beta, a geopolitics specialist authoring binary "
    "prediction-market questions for Polymarket. Your edge is "
    "multi-source verification: every resolution criterion must name at "
    "least two corroborating outlets that include an official government "
    "channel (State Department briefings, EU Council press releases, "
    "PRC MOFA spokesperson statements, UNSC resolutions) alongside one "
    "or more wire services (Reuters, AP, Xinhua, RIA Novosti). Frame "
    "questions with neutral wording — no leading verbs, no implied "
    "expectation of YES or NO. Output STRICT JSON only — no markdown, "
    "no prose."
)


# ---------------------------------------------------------------------------
# Bid-strategy constants
# ---------------------------------------------------------------------------

_HOME_TURF_BID = 0.32     # ``geopolitics/*``
_SIBLING_BID = 0.60       # ``macro/*``
_AWAY_BID = 0.80          # anything else


def _primary_category(event: Dict[str, Any]) -> str:
    scoring = event.get("scoring")
    if isinstance(scoring, dict):
        prim = scoring.get("primary_category")
        if isinstance(prim, str) and prim.strip():
            return prim.strip().lower()
    category = event.get("category")
    if isinstance(category, str) and category.strip():
        return category.strip().lower()
    return ""


class SeederBeta(BaseTranslatorAgent):
    """Geopolitics-specialist reference seeder. Verification-first."""

    AGENT_NAME = "deepseek"
    DISPLAY_NAME = "Seeder Beta"
    MODEL_ID = CLAUDE_HAIKU
    TEMPERATURE = 0.5

    BID_MIN_USDC = 0.32
    BID_MAX_USDC = 0.80

    def __init__(
        self,
        wallet_pk: str,
        *,
        llm_factory: Optional[Any] = None,
        reputation_history: Optional[float] = None,
        onchain: Optional[Any] = None,
    ) -> None:
        factory = llm_factory or (
            lambda: make_llm(
                self.MODEL_ID,
                system=_SEEDER_BETA_SYSTEM,
                temperature=self.TEMPERATURE,
                max_tokens=DEFAULT_MAX_TOKENS,
            )
        )
        super().__init__(
            wallet_pk=wallet_pk,
            llm_factory=factory,
            reputation_history=reputation_history,
            onchain=onchain,
        )

    def bid_strategy(self, event: Dict[str, Any]) -> float:
        """Geopolitics home-turf policy.

        * ``geopolitics/*`` -> 0.32 USDC.
        * ``macro/*`` -> 0.60.
        * Anything else -> 0.80.
        """

        primary = _primary_category(event)
        if primary.startswith("geopolitics"):
            bid = _HOME_TURF_BID
        elif primary.startswith("macro"):
            bid = _SIBLING_BID
        else:
            bid = _AWAY_BID
        bid = max(self.BID_MIN_USDC, min(self.BID_MAX_USDC, bid))
        return round(bid, 4)


# Backwards-compatible alias.
DeepSeekAgent = SeederBeta

__all__ = ["DeepSeekAgent", "SeederBeta"]
