"""``SeederAlpha`` — macro-specialist reference seeder.

Identity is intentionally neutral: the on-disk wallet slot stays as
``"gemini"`` (so deterministic wallet derivation, existing on-chain
reputation, and historical bid records remain stable) but the surfaced
agent name is ``seeder_alpha`` and the class no longer references any
underlying provider in its public API.

Specialty: macroeconomics, central banks, monetary policy, GDP,
inflation. The system prompt prefers monetary aggregates / interest
rates / FX as resolution sources, and the bid strategy reads
``event["scoring"]["primary_category"]`` (slash-separated, e.g.
``macro/china_monetary``) to bid aggressively on its home turf.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..llm import (
    CLAUDE_HAIKU,
    DEFAULT_MAX_TOKENS,
    LLMCallable,
    make_llm,
)
from .base import BaseTranslatorAgent

# ---------------------------------------------------------------------------
# Persona prompt
# ---------------------------------------------------------------------------

_SEEDER_ALPHA_SYSTEM = (
    "You are Seeder Alpha, a macroeconomics specialist authoring binary "
    "prediction-market questions for Polymarket. Your edge is precise "
    "macro resolution criteria: name central-bank publications "
    "(Federal Reserve H.15, ECB statistical data warehouse, PBoC monetary "
    "policy reports, BLS CPI release), specify the exact instrument "
    "(federal funds target range upper bound, MLF rate, 10-yr UST yield, "
    "DXY close, EUR/USD spot), and pin the cutoff to a known release date. "
    "Prefer monetary aggregates / policy rates / official FX prints over "
    "second-hand reporting. Output STRICT JSON only — no markdown, no prose."
)


# ---------------------------------------------------------------------------
# Bid-strategy constants
# ---------------------------------------------------------------------------

# Aggressive bid when the event lands inside this agent's specialty.
_HOME_TURF_BID = 0.30
# Less confident when the event is in a sibling category.
_SIBLING_BID = 0.65
# Highest bid when the event is well outside specialty.
_AWAY_BID = 0.85


def _primary_category(event: Dict[str, Any]) -> str:
    """Best-effort extraction of the slash-separated category path.

    Falls back to the top-level ``category`` string if scoring is absent.
    """

    scoring = event.get("scoring")
    if isinstance(scoring, dict):
        prim = scoring.get("primary_category")
        if isinstance(prim, str) and prim.strip():
            return prim.strip().lower()
    category = event.get("category")
    if isinstance(category, str) and category.strip():
        return category.strip().lower()
    return ""


class SeederAlpha(BaseTranslatorAgent):
    """Macro-specialist reference seeder. Cheap, deterministic, decisive."""

    # Slot name kept as "gemini" so deterministic wallet derivation
    # (``derive_agent_wallet(operator_pk, "gemini")``), the orchestrator's
    # agent_names tuple, and outputs/agent_wallets.json stay stable.
    AGENT_NAME = "gemini"
    DISPLAY_NAME = "Seeder Alpha"
    MODEL_ID = CLAUDE_HAIKU
    TEMPERATURE = 0.3

    # Bid window — kept wide enough to cover all three bid strategies below.
    BID_MIN_USDC = 0.30
    BID_MAX_USDC = 0.85

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
                system=_SEEDER_ALPHA_SYSTEM,
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
        """Macro home-turf policy.

        * ``macro/*`` -> 0.30 USDC (very confident).
        * ``geopolitics/*`` -> 0.65 (sibling).
        * Anything else -> 0.85.
        """

        primary = _primary_category(event)
        if primary.startswith("macro"):
            bid = _HOME_TURF_BID
        elif primary.startswith("geopolitics"):
            bid = _SIBLING_BID
        else:
            bid = _AWAY_BID
        # Defensive clamp so the band never escapes the configured window.
        bid = max(self.BID_MIN_USDC, min(self.BID_MAX_USDC, bid))
        return round(bid, 4)


# Backwards-compatible alias so legacy imports (``from .gemini_agent
# import GeminiAgent``) keep working until the dispatcher migration
# finishes. New code should import ``SeederAlpha`` directly.
GeminiAgent = SeederAlpha

__all__ = ["GeminiAgent", "SeederAlpha"]
