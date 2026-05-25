"""GeminiAgent: lowest-bid bold strategy.

Bids at the bottom of the band (0.30-0.50 USDC) to maximise win rate, at
the cost of thin margin. Confidence scales the bid linearly.
"""

from __future__ import annotations

from typing import Any, Dict

from ..llm import GEMINI_FLASH
from .base import BaseTranslatorAgent


class GeminiAgent(BaseTranslatorAgent):
    MODEL_ID = GEMINI_FLASH
    AGENT_NAME = "gemini"
    BID_MIN_USDC = 0.30
    BID_MAX_USDC = 0.50

    def bid_strategy(self, event: Dict[str, Any]) -> float:
        """Bold low-margin policy.

        * Long, content-rich events -> bid near the top of the (low) band.
        * Short/cheap events -> bid the floor.
        """

        body_len = len(str(event.get("body_zh") or event.get("body") or ""))
        # Saturates at 2000 chars.
        confidence = min(1.0, body_len / 2000.0)
        spread = self.BID_MAX_USDC - self.BID_MIN_USDC
        return round(self.BID_MIN_USDC + spread * confidence, 4)
