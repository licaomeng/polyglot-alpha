"""LlamaAgent: conservative high-margin strategy.

Bids in the 0.80-1.20 USDC band. Wins less often but cashes in when it
does. Bid is reputation-anchored: higher reputation -> bid slightly higher
since the auction's reputation-adjustment still keeps the score competitive.
"""

from __future__ import annotations

from typing import Any, Dict

from ..llm import LLAMA_33
from .base import BaseTranslatorAgent


class LlamaAgent(BaseTranslatorAgent):
    MODEL_ID = LLAMA_33
    AGENT_NAME = "llama"
    BID_MIN_USDC = 0.80
    BID_MAX_USDC = 1.20

    def bid_strategy(self, event: Dict[str, Any]) -> float:
        """Conservative policy.

        Bid scales with the agent's reputation: a well-reputed agent can
        push the price higher because the auction divides by reputation.
        """

        rep = getattr(self, "reputation_history", 1.0) or 1.0
        # Map reputation [0.5, 3.0] -> position [0, 1] inside the band.
        normalized = max(0.0, min(1.0, (rep - 0.5) / 2.5))
        spread = self.BID_MAX_USDC - self.BID_MIN_USDC
        return round(self.BID_MIN_USDC + spread * normalized, 4)
