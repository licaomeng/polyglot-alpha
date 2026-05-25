"""Compare an agent-generated question against an actual resolved market.

Three signals are computed:

1. **Semantic similarity** — sentence-transformers cosine between the
   two question strings. Falls back to a deterministic character-bag
   Jaccard score when ``sentence-transformers`` is unavailable / disabled.
2. **Framing match** — does the agent's YES framing imply the same
   outcome that actually resolved? We look at whether the agent's
   question contains an "above/over/will-X-happen" framing (implicit
   YES bet) and compare against the actual resolution.
3. **Resolution agreement** — boolean: ``framing_predicted == actual``.

The matcher is intentionally heuristic — these are reverse-engineered
historical signals, not arbitration. Calibration knobs live as module
constants so the reporter can surface them.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

# Cached embedder; loaded lazily because sentence-transformers pulls in
# torch which is heavy.
_EMBEDDER = None
_EMBEDDER_TRIED = False


SEMANTIC_MATCH_THRESHOLD = 0.55
"""Cosine threshold above which we count two questions as semantically equivalent."""

# Words that imply a YES-leaning framing (the question expects the event to happen).
_YES_FRAMING_TOKENS: tuple[str, ...] = (
    "will ",
    "above ",
    "over ",
    "exceed",
    "reach ",
    "hit ",
    "before ",
    "by ",
    "announce",
    "approve",
    "win ",
)
# Words that imply a NO-leaning framing (the question expects the event NOT to happen).
_NO_FRAMING_TOKENS: tuple[str, ...] = (
    "fail ",
    "below ",
    "under ",
    "lose ",
    "miss ",
    "reject",
    "never ",
)

YES_OUTCOMES = frozenset({"YES", "Yes", "yes"})
NO_OUTCOMES = frozenset({"NO", "No", "no"})


@dataclass(frozen=True)
class OutcomeComparison:
    """Result of comparing one agent question vs one actual market."""

    semantic_similarity: float
    semantic_match: bool
    framing_predicted: str  # "YES" | "NO" | "UNKNOWN"
    outcome_match: bool
    notes: str

    def as_dict(self) -> dict:
        return asdict(self)


def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace; strip punctuation noise."""

    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip().lower())


def _jaccard_similarity(a: str, b: str) -> float:
    """Deterministic fallback similarity: token-bag Jaccard."""

    a_tokens = set(re.findall(r"[a-z0-9]+", _normalize(a)))
    b_tokens = set(re.findall(r"[a-z0-9]+", _normalize(b)))
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = a_tokens & b_tokens
    union = a_tokens | b_tokens
    return len(intersection) / len(union)


def _get_embedder():
    """Lazy-load sentence-transformers. Returns ``None`` on failure."""

    global _EMBEDDER, _EMBEDDER_TRIED
    if _EMBEDDER_TRIED:
        return _EMBEDDER
    _EMBEDDER_TRIED = True
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        _EMBEDDER = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    except Exception:
        _EMBEDDER = None
    return _EMBEDDER


def semantic_similarity(a: str, b: str, *, use_embeddings: bool = True) -> float:
    """Cosine similarity in [0, 1]; falls back to Jaccard."""

    if not a or not b:
        return 0.0
    if use_embeddings:
        embedder = _get_embedder()
        if embedder is not None:
            try:
                import numpy as np  # local import keeps top-level light

                vectors = embedder.encode([a, b], normalize_embeddings=True)
                cosine = float(np.dot(vectors[0], vectors[1]))
                # Clamp negatives (rare with normalized MiniLM) to 0.
                return max(0.0, min(1.0, cosine))
            except Exception:
                pass
    return _jaccard_similarity(a, b)


def infer_framing(question: str) -> str:
    """Return ``"YES"`` / ``"NO"`` / ``"UNKNOWN"`` for the implicit framing.

    Most Polymarket-style questions are phrased so YES = event happens,
    but we still look for explicit NO-cues to flag inverted phrasings.
    """

    text = _normalize(question)
    if not text:
        return "UNKNOWN"
    yes_hits = sum(1 for tok in _YES_FRAMING_TOKENS if tok in text)
    no_hits = sum(1 for tok in _NO_FRAMING_TOKENS if tok in text)
    if yes_hits == 0 and no_hits == 0:
        return "UNKNOWN"
    if no_hits > yes_hits:
        return "NO"
    return "YES"


def _normalize_outcome(outcome: str) -> str:
    if outcome in YES_OUTCOMES:
        return "YES"
    if outcome in NO_OUTCOMES:
        return "NO"
    return "OTHER"


def compare_questions(
    agent_question: str,
    actual_question: str,
    actual_outcome: str,
    *,
    use_embeddings: bool = True,
) -> OutcomeComparison:
    """Compare one agent-generated question to one resolved market.

    Args:
        agent_question: The winner's synthesized question text.
        actual_question: The historical Polymarket question text.
        actual_outcome: The historical resolution (``"YES"`` / ``"NO"``
            / other).
        use_embeddings: Disable to force the deterministic Jaccard path
            (used in tests).
    """

    similarity = semantic_similarity(
        agent_question, actual_question, use_embeddings=use_embeddings
    )
    semantic_match = similarity >= SEMANTIC_MATCH_THRESHOLD
    framing = infer_framing(agent_question)
    normalized_actual = _normalize_outcome(actual_outcome)

    if framing == "UNKNOWN" or normalized_actual == "OTHER":
        outcome_match = False
        notes_parts = []
        if framing == "UNKNOWN":
            notes_parts.append("framing inference inconclusive")
        if normalized_actual == "OTHER":
            notes_parts.append(f"non-binary outcome={actual_outcome!r}")
        notes = "; ".join(notes_parts)
    else:
        outcome_match = framing == normalized_actual
        notes = (
            f"framing={framing} vs actual={normalized_actual} -> "
            f"{'match' if outcome_match else 'miss'}"
        )

    return OutcomeComparison(
        semantic_similarity=similarity,
        semantic_match=semantic_match,
        framing_predicted=framing,
        outcome_match=outcome_match,
        notes=notes,
    )


def infer_category(question: str) -> str:
    """Heuristic category bucket; the source dataset's category column is empty."""

    text = _normalize(question)
    rules: Iterable[tuple[str, tuple[str, ...]]] = (
        ("crypto", ("btc", "bitcoin", "ethereum", "fdv", "token", "airdrop", "launch", "blockchain")),
        ("politics", ("president", "election", "congress", "senate", "trump", "biden", "pm ", "minister")),
        ("sports", ("nba", "nfl", "mlb", "cup", "match", "vs ", "race", "tennis", "soccer", "football")),
        ("economics", ("fed", "rate", "cpi", "gdp", "inflation", "recession", "unemployment", "interest")),
        ("policy", ("policy", "regulation", "approve", "ban", "law", "tariff", "sanction")),
        ("tech", ("ai ", "openai", "apple", "google", "microsoft", "amazon", "tesla", "spacex")),
    )
    for category, keywords in rules:
        if any(kw in text for kw in keywords):
            return category
    return "other"
