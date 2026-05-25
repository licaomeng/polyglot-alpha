"""Polymarket V2 integration: submission, builder code, fill listener.

Provides a unified async API for:
  - Submitting forecasting questions to Polymarket V2.
  - Registering & resolving builder codes (real and demo modes).
  - Listening for fills and crediting the on-chain BuilderFeeRouter.

All public symbols are re-exported here for ergonomic imports:

    from polyglot_alpha.polymarket import PolymarketV2Client, Question
"""

from polyglot_alpha.polymarket.types import (
    BuilderFeeEvent,
    Fill,
    PolymarketMode,
    Question,
    SubmissionResult,
)
from polyglot_alpha.polymarket.builder_code import (
    BUILDER_CODE_LENGTH,
    register_builder_code,
    resolve_translator_for_code,
)
from polyglot_alpha.polymarket.client import PolymarketV2Client
from polyglot_alpha.polymarket.mock_client import MockPolymarketClient
from polyglot_alpha.polymarket.fill_listener import FillListener

__all__ = [
    "BUILDER_CODE_LENGTH",
    "BuilderFeeEvent",
    "Fill",
    "FillListener",
    "MockPolymarketClient",
    "PolymarketMode",
    "PolymarketV2Client",
    "Question",
    "SubmissionResult",
    "register_builder_code",
    "resolve_translator_for_code",
]
