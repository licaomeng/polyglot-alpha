"""Central registry of known LLM-fallback stub strings.

W14-FIX-STUB closed 5 silent-stub gates where an LLM glitch (empty or
unparseable output) caused the pipeline to insert generic placeholder
text and silently propagate it downstream. This module is the single
source of truth for those placeholder strings so detection is
consistent across the 5 sites (``translators``, ``synthesizer``,
``quality_eval``, ``polymarket.client``, and any future caller).

When you add a new fallback string in the pipeline, append it to
``KNOWN_STUB_PHRASES`` here so the gate keeps catching it.
"""

from __future__ import annotations

from typing import Iterable, Optional

# Generic placeholders emitted by ``translators.propose_candidates`` when
# the LLM returns empty / unparseable JSON. Anything in this set is a
# guaranteed LLM-glitch artifact and must NEVER reach Polymarket.
KNOWN_STUB_PHRASES: frozenset[str] = frozenset(
    {
        "Will the event resolve as expected?",
        "Resolves YES if the event occurs by the cutoff.",
    }
)


def is_stub(text: Optional[str]) -> bool:
    """Return ``True`` when ``text`` matches a known stub placeholder.

    Matching is exact (after ``strip()``); we do NOT substring-match to
    avoid false positives on legitimately phrased market questions.
    """

    if not text:
        return False
    return text.strip() in KNOWN_STUB_PHRASES


def any_stub(*texts: Optional[str]) -> bool:
    """Return ``True`` if any of ``texts`` is a known stub placeholder."""

    return any(is_stub(t) for t in texts)


def stub_reason(texts: Iterable[Optional[str]]) -> Optional[str]:
    """Return the first matching stub phrase, or ``None`` if none match.

    Useful for logging which specific placeholder leaked through.
    """

    for t in texts:
        if is_stub(t):
            return (t or "").strip()
    return None
