"""Public SDK for external operators who want to register their own agent
on Polyglot Alpha.

The protocol is intentionally thin: the only contract an operator has to
satisfy is "produce a candidate question, sign a bid that hashes the
candidate, and submit it to ``TranslationAuction`` before the auction
window closes." Everything else — how the candidate is generated, what
LLM is used, whether multiple rounds of self-critique are run — is the
operator's choice.

External operators implement an agent that:

1. Reads an event payload (raw news + scoring metadata) of shape
   :class:`EventPayload`.
2. Generates a candidate Polymarket question of shape
   :class:`CandidateQuestion` using their method, any LLM.
3. Signs and submits a bid + candidate_hash to ``TranslationAuction``.

They do NOT need to use our internal debate loop. They can use single-shot
completions, retrieval-augmented generation, fine-tuned models, rule-based
templating, anything. The four reference seeders we ship (gemini,
deepseek, qwen, llama) all use :func:`run_internal_debate` because we
found it improves quality empirically — but that's our implementation
detail, not a protocol rule.

If an operator DOES want to reuse the reference seeder's debate loop,
they can import :func:`run_internal_debate` from this module and plug in
their own ``propose_candidates_fn``.
"""

from polyglot_alpha.agents.base import (
    BaseTranslatorAgent as BaseAgent,
    BidIntent,
    CandidateQuestion,
    EventPayload,
)
from polyglot_alpha.agents.internal_debate import (
    InternalDebateResult,
    run_internal_debate,
)

__all__ = [
    "BaseAgent",
    "BidIntent",
    "CandidateQuestion",
    "EventPayload",
    "InternalDebateResult",
    "run_internal_debate",
]
