"""DeepSeekAgent: cautious mid-range strategy.

Bids in the 0.60-0.90 USDC band — confident in its quality so it asks for
more, but never the highest.
"""

from __future__ import annotations

from typing import Any, Dict

from ..llm import DEEPSEEK_V3
from .base import BaseTranslatorAgent


class DeepSeekAgent(BaseTranslatorAgent):
    MODEL_ID = DEEPSEEK_V3
    AGENT_NAME = "deepseek"
    BID_MIN_USDC = 0.60
    BID_MAX_USDC = 0.90

    def bid_strategy(self, event: Dict[str, Any]) -> float:
        """Cautious policy: bid the midpoint, nudged by historical reputation.

        Higher reputation pulls the bid down (the auction is
        reputation-weighted: score = bid / max(reputation, 1.0), so a
        well-reputed agent can afford a smaller absolute number).
        """

        midpoint = (self.BID_MIN_USDC + self.BID_MAX_USDC) / 2.0
        # ``reputation_history`` lives on the agent; default 1.0.
        rep = getattr(self, "reputation_history", 1.0) or 1.0
        # Clamp the discount factor so we never bid below the floor.
        discount = max(0.5, min(1.0, 1.0 / rep))
        bid = midpoint * discount
        bid = max(self.BID_MIN_USDC, min(self.BID_MAX_USDC, bid))
        return round(bid, 4)
