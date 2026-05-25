"""QwenAgent: opportunistic topic-aware strategy.

Bid varies widely (0.30-1.20 USDC) based on event topic. Strong on
Chinese-language content (it is a Chinese model) so it bids low on
domestic topics where it expects to win quality, and high on niche
topics where it wants compensation for the extra effort.
"""

from __future__ import annotations

from typing import Any, Dict

from ..llm import QWEN_25
from .base import BaseTranslatorAgent


# Lower bid = more eager to win that topic.
_TOPIC_BID_OVERRIDES: dict[str, float] = {
    "geopolitics": 0.40,
    "china": 0.30,
    "finance": 0.70,
    "tech": 0.85,
    "sports": 0.90,
    "entertainment": 1.10,
}
_DEFAULT_BID = 0.75


class QwenAgent(BaseTranslatorAgent):
    MODEL_ID = QWEN_25
    AGENT_NAME = "qwen"
    BID_MIN_USDC = 0.30
    BID_MAX_USDC = 1.20

    def bid_strategy(self, event: Dict[str, Any]) -> float:
        """Topic-conditioned policy.

        Looks at ``event['topic']`` (string) or, failing that, the first
        matching keyword in the body. Falls back to the band midpoint.
        """

        topic_raw = event.get("topic") or self._infer_topic(event)
        topic = str(topic_raw or "").lower()
        bid = _TOPIC_BID_OVERRIDES.get(topic, _DEFAULT_BID)
        bid = max(self.BID_MIN_USDC, min(self.BID_MAX_USDC, bid))
        return round(bid, 4)

    @staticmethod
    def _infer_topic(event: Dict[str, Any]) -> str:
        body = str(event.get("body_zh") or event.get("body") or "")
        # Quick keyword scan — cheap and good enough for the demo.
        for topic in _TOPIC_BID_OVERRIDES:
            if topic in body.lower():
                return topic
        return ""
