"""Real on-chain clients for the PolyglotAlpha v2 contract suite.

This package is a thin async-friendly facade over :mod:`polyglot_alpha.onchain`'s
``OnChainClient`` (a synchronous web3.py wrapper). Each module here exposes
the surface the orchestrator and agents call:

* :mod:`.auction_client` — open/collect/settle the ``TranslationAuction``.
* :mod:`.question_registry` — commit a passed question via
  ``QuestionRegistry.registerQuestion``.
* :mod:`.builder_fee_router` — credit fills + claim accrued fees.
* :mod:`.reputation_registry` — read or update reputation scores.

All public functions are ``async`` so the orchestrator can await them; under
the hood blocking RPC calls run inside ``loop.run_in_executor`` to keep the
event loop responsive.

All four modules degrade gracefully when the chain is unreachable: they
raise the underlying ``web3``/``httpx`` exception so callers can decide
whether to fall back to a deterministic mock or surface the error.
"""

from __future__ import annotations

from . import auction_client, builder_fee_router, question_registry, reputation_registry
from .auction_client import AuctionClient
from .builder_fee_router import BuilderFeeRouter
from .question_registry import QuestionRegistry
from .reputation_registry import ReputationRegistryClient

__all__ = [
    "AuctionClient",
    "BuilderFeeRouter",
    "QuestionRegistry",
    "ReputationRegistryClient",
    "auction_client",
    "builder_fee_router",
    "question_registry",
    "reputation_registry",
]
