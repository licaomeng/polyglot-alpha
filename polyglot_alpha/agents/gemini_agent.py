"""``GeminiAgent`` (legacy class name): the 4th translator agent, now
backed by OpenRouter's Mistral Large.

History: this slot used to call Google's Gemini 2.0 Flash directly. The
free-tier quota burned through quickly and every 429 caused the
``dispatch.run_for_winner`` fallback to emit a synthetic ``"<title>?"``
translation that was committed on-chain as if it were a real LLM output.
Routing this agent through OpenRouter (same provider the other three
agents already use) removes the 429 source entirely.

The registry key, ``AGENT_NAME``, the on-disk wallet, and the orchestrator
references all still spell this agent ``gemini`` — that is the slot name,
not the model — so backtest fixtures, derived agent wallets, and
historical bid records remain stable. The actual LLM backing the slot is
controlled by ``MODEL_ID`` below.

Bid policy: lowest-bid bold strategy (0.30-0.50 USDC), unchanged.
"""

from __future__ import annotations

from typing import Any, Dict

from ..llm import MISTRAL_LARGE
from .base import BaseTranslatorAgent


class GeminiAgent(BaseTranslatorAgent):
    # Slot name kept as "gemini" so deterministic wallet derivation
    # (``derive_agent_wallet(operator_pk, "gemini")``) and the orchestrator's
    # ``agent_names`` tuple stay stable. The MODEL_ID drives which LLM the
    # pipeline actually calls.
    MODEL_ID = MISTRAL_LARGE
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
