"""``SeederGamma`` — markets / sentiment-specialist reference seeder.

Specialty: market microstructure, sentiment shifts, liquidity events,
equity moves. The system prompt prefers market-data sources (Bloomberg,
Reuters market data, exchange print files) and pushes resolution cutoff
windows tighter than the macro/geopolitics seeders. Temperature is the
highest of the three (0.7) so wording can pick up on tactical
phrasing.

Wallet slot remains ``"qwen"`` so deterministic wallet derivation +
historical bid records keep working.
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

_SEEDER_GAMMA_SYSTEM = (
    "You are Seeder Gamma, a markets and sentiment specialist authoring "
    "binary prediction-market questions for Polymarket. Your edge is "
    "tight resolution windows tied to market prints: name specific "
    "tickers / indices (SPX, NDX, HSI, .N225), use exchange close prices "
    "(NYSE 16:00 ET, HKEX 16:00 HKT, TSE 15:00 JST), prefer market-data "
    "vendors (Bloomberg BBG, Refinitiv RIC, FactSet) as primary "
    "resolution sources. Push the cutoff to the next session close when "
    "the news warrants a same-week resolution. Output STRICT JSON only "
    "— no markdown, no prose."
)


# ---------------------------------------------------------------------------
# Bid-strategy constants
# ---------------------------------------------------------------------------

_HOME_TURF_BID = 0.28     # ``markets/*`` or equity / sentiment keywords
_AWAY_BID = 0.75          # everything else

# Keywords that strongly hint at a markets / sentiment event even when the
# primary_category is something else (e.g. an Apple earnings beat scored
# as ``corporate/earnings`` should still be home-turf for this agent).
_MARKETS_KEYWORDS: tuple[str, ...] = (
    "equity",
    "stock",
    "stocks",
    "shares",
    "share price",
    "earnings",
    "guidance",
    "ipo",
    "buyback",
    "yield",
    "bond",
    "vix",
    "spx",
    "nasdaq",
    "s&p",
    "ftse",
    "nikkei",
    "hang seng",
    "sentiment",
    "rally",
    "selloff",
    "sell-off",
    "liquidity",
)


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


def _matches_markets_keywords(event: Dict[str, Any]) -> bool:
    """Return True if the event body / title carries markets keywords."""

    haystack = " ".join(
        str(event.get(field) or "")
        for field in ("title", "title_zh", "body", "body_zh", "summary")
    ).lower()
    if not haystack:
        return False
    return any(kw in haystack for kw in _MARKETS_KEYWORDS)


class SeederGamma(BaseTranslatorAgent):
    """Markets / sentiment-specialist reference seeder. Tight cutoffs."""

    AGENT_NAME = "qwen"
    DISPLAY_NAME = "Seeder Gamma"
    MODEL_ID = CLAUDE_HAIKU
    TEMPERATURE = 0.7

    BID_MIN_USDC = 0.28
    BID_MAX_USDC = 0.75

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
                system=_SEEDER_GAMMA_SYSTEM,
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
        """Markets home-turf policy.

        * ``markets/*`` or any equity / sentiment-keyword event ->
          0.28 USDC.
        * Anything else -> 0.75.
        """

        primary = _primary_category(event)
        if primary.startswith("markets") or _matches_markets_keywords(event):
            bid = _HOME_TURF_BID
        else:
            bid = _AWAY_BID
        bid = max(self.BID_MIN_USDC, min(self.BID_MAX_USDC, bid))
        return round(bid, 4)


# Backwards-compatible alias.
QwenAgent = SeederGamma

__all__ = ["QwenAgent", "SeederGamma"]
