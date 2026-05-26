"""Claude Haiku event-scoring layer for the marketplace.

Scope (post 2026-05-26 protocol/seeder pivot)
---------------------------------------------
PolyglotAlpha is now a *protocol* — auction + 11-judge panel + Polymarket
integration + on-chain fee routing. The marketplace MUST NOT generate
Polymarket question text: each agent (whether one of the 4 reference
seeders or an external operator) does its own question framing — that's
their value-add and how they differentiate.

What this module does (allowed):
    * Quality-score a raw news cluster on whether it's worth opening an
      auction at all.
    * Categorize, extract key entities, gauge source credibility &
      timeliness, and emit a short neutral summary.
    * Reject events that fall below the quality bar with a human-readable
      ``rejection_reason``.

What this module does NOT do (forbidden):
    * Write Polymarket question text.
    * Pick a "selected_index" / produce candidate questions.
    * Emit ``polymarket_question`` / ``resolution_criteria`` /
      ``cutoff_iso`` fields.

The output of :func:`score_event_for_auction` is metadata only; the agents
downstream consume the raw cluster (title, summary, sources) plus this
scoring dict and write their own questions during the auction.

Module is intentionally tolerant: missing ``ANTHROPIC_API_KEY``, network
failures, or malformed JSON output never raise — the caller gets a low-
score :class:`EventScoring` with ``rejection_reason`` set so the trigger
endpoint can degrade gracefully.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import MODEL_NEWS_SCORER

LOGGER = logging.getLogger(__name__)

# News-scorer model. Configured by :data:`polyglot_alpha.models.MODEL_NEWS_SCORER`
# (env var ``MODEL_NEWS_SCORER``, default Haiku 4.5 — ~8x cheaper than
# Sonnet at the same throughput floor for ~1k-token scoring tasks).
HAIKU_MODEL: str = MODEL_NEWS_SCORER

# Hard caps so a runaway feed does not blow up the prompt.
MAX_ARTICLES: int = 20
MAX_TITLE_CHARS: int = 200
MAX_SUMMARY_CHARS: int = 400

# Auction-quality threshold. Below this, the trigger endpoint should
# reject the cluster (or surface the rejection_reason to the operator).
MIN_AUCTION_QUALITY: float = 0.5

# Bounded-score categories. The model is free to use any string under
# ``primary_category`` but we whitelist the top-level prefix so we can
# index/route on it.
_KNOWN_TOP_CATEGORIES: tuple[str, ...] = (
    "macro",
    "geopolitics",
    "tech",
    "policy",
    "energy",
    "finance",
    "hk",
    "taiwan",
    "other",
)


@dataclass(frozen=True)
class EventScoring:
    """Marketplace-side scoring of a raw news cluster.

    This is intentionally question-free: it tells the orchestrator
    whether the cluster is worth opening an auction at all, and gives
    agents enough metadata (category, entities, source credibility,
    timeliness) to write their own Polymarket question.

    Fields
    ------
    event_quality_score:
        0-1, higher = more market-actionable. Below
        :data:`MIN_AUCTION_QUALITY` the trigger endpoint should reject.
    primary_category:
        Slash-separated category path, e.g. ``"macro/china_monetary"``.
    sub_categories:
        Additional tags. May be empty.
    key_entities:
        Named entities (orgs, people, instruments) referenced by the
        cluster. Helps agents draft a specific question.
    source_credibility:
        0-1, higher = more trustworthy sources. Multi-source clusters
        from official outlets score high; single-source rumor low.
    timeliness_score:
        0-1, higher = more recent / fresher information.
    raw_summary:
        2-3 sentence neutral description of the cluster. Suitable for UI
        display and as agent context.
    rejection_reason:
        Set iff ``event_quality_score < MIN_AUCTION_QUALITY``. Explains
        in plain English why the event isn't auction-worthy (e.g. "pure
        opinion piece with no clean public resolution feed").
    """

    event_quality_score: float
    primary_category: str
    sub_categories: list[str]
    key_entities: list[str]
    source_credibility: float
    timeliness_score: float
    raw_summary: str
    rejection_reason: Optional[str] = None
    model: str = field(default=HAIKU_MODEL)

    def as_dict(self) -> dict[str, Any]:
        """Project to a JSON-serializable dict for ``event_dict['scoring']``."""

        return asdict(self)


def _format_articles_for_prompt(articles: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for idx, art in enumerate(articles[:MAX_ARTICLES]):
        title = str(art.get("title") or "").strip()[:MAX_TITLE_CHARS]
        summary = str(art.get("summary") or "").strip()[:MAX_SUMMARY_CHARS]
        source = str(art.get("source") or "unknown")
        pub = str(art.get("published") or "")
        url = str(art.get("url") or "")
        lines.append(
            f"[{idx + 1}] source={source} published={pub}\n"
            f"    title: {title}\n"
            f"    summary: {summary}\n"
            f"    url: {url}"
        )
    return "\n".join(lines)


def _build_scoring_prompt(articles: list[dict[str, Any]]) -> str:
    """Render the scoring prompt for Claude Haiku.

    The schema is documented in the JSON skeleton at the bottom of the
    prompt; we ask Haiku to fill it in verbatim so the response is easy
    to parse with ``json.loads`` after stripping any code fences.

    The prompt deliberately does NOT ask for any question text — only
    metadata describing the news cluster.
    """

    today = datetime.now(tz=timezone.utc).date().isoformat()
    article_block = _format_articles_for_prompt(articles)
    categories = ", ".join(_KNOWN_TOP_CATEGORIES)
    return f"""You are a news triage analyst for a prediction-market protocol.
Today is {today}. You will receive a cluster of related news items.

Your ONLY job: score this cluster as event metadata. You MUST NOT write
any prediction-market question, headline rewrite, or resolution clause.
Agents downstream of you write the question themselves — your job is to
tell them whether the event is auction-worthy and what it's about.

Score the cluster on:
  - event_quality_score (0.0 - 1.0): how market-actionable. 1.0 = a
    clear, time-bound, externally-verifiable development (rate cuts,
    sanctions, elections, M&A); 0.1 = pure opinion / cultural / human-
    interest piece with no clean resolution feed.
  - primary_category: slash-separated path, e.g. ``macro/china_monetary``,
    ``geopolitics/taiwan_strait``, ``policy/china_regulation``. Top-level
    must be one of: {categories}.
  - sub_categories: optional tags (max 5).
  - key_entities: named entities (orgs, people, instruments, countries)
    that any auction question would have to mention. Max 8.
  - source_credibility (0.0 - 1.0): trustworthiness of the sourcing.
    Multi-source / official-outlet -> high. Single-source rumor / blog -> low.
  - timeliness_score (0.0 - 1.0): freshness. Today / yesterday -> 1.0;
    older than a week -> ~0.3.
  - raw_summary: 2-3 neutral sentences describing what happened. No
    speculation, no question framing, no "will X happen by Y" phrasing.
  - rejection_reason: required iff event_quality_score < {MIN_AUCTION_QUALITY:.2f},
    otherwise null. One sentence explaining why this event isn't worth
    an auction (e.g. "pure opinion piece, no public resolution feed").

NEWS ITEMS:
{article_block}

Respond with STRICT JSON only — no prose, no code fences, no question text:
{{
  "event_quality_score": 0.0,
  "primary_category": "...",
  "sub_categories": ["..."],
  "key_entities": ["..."],
  "source_credibility": 0.0,
  "timeliness_score": 0.0,
  "raw_summary": "...",
  "rejection_reason": null
}}
"""


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    """Remove ```json fences Haiku occasionally adds despite the prompt."""

    return _CODE_FENCE_RE.sub("", text).strip()


def _clamp_unit(value: Any, default: float = 0.0) -> float:
    """Clamp arbitrary input into ``[0.0, 1.0]`` with a safe default."""

    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _coerce_str_list(value: Any, *, cap: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, (str, int, float)):
            continue
        s = str(item).strip()
        if s:
            out.append(s)
        if len(out) >= cap:
            break
    return out


def _validate_scoring_payload(raw: dict[str, Any]) -> EventScoring:
    """Lightly normalize Haiku output into an :class:`EventScoring`.

    Defensive against missing/wrong types; always returns a valid
    dataclass. If ``event_quality_score`` falls below
    :data:`MIN_AUCTION_QUALITY` and no ``rejection_reason`` was provided,
    we synthesize one so downstream callers always have a reason string.
    """

    score = _clamp_unit(raw.get("event_quality_score"), default=0.0)
    primary = str(raw.get("primary_category") or "other").strip() or "other"
    sub = _coerce_str_list(raw.get("sub_categories"), cap=5)
    entities = _coerce_str_list(raw.get("key_entities"), cap=8)
    credibility = _clamp_unit(raw.get("source_credibility"), default=0.5)
    timeliness = _clamp_unit(raw.get("timeliness_score"), default=0.5)
    summary = str(raw.get("raw_summary") or "").strip()

    rejection_raw = raw.get("rejection_reason")
    rejection: Optional[str]
    if rejection_raw in (None, "", "null"):
        rejection = None
    else:
        rejection = str(rejection_raw).strip() or None

    if score < MIN_AUCTION_QUALITY and rejection is None:
        rejection = (
            f"event_quality_score={score:.2f} below auction threshold "
            f"{MIN_AUCTION_QUALITY:.2f}"
        )

    return EventScoring(
        event_quality_score=score,
        primary_category=primary,
        sub_categories=sub,
        key_entities=entities,
        source_credibility=credibility,
        timeliness_score=timeliness,
        raw_summary=summary,
        rejection_reason=rejection,
    )


def _try_parse_partial_json(text: str) -> Optional[dict[str, Any]]:
    """Attempt to recover a valid JSON object even if Haiku truncated.

    Truncation typically clips trailing closing braces. We scan from the
    rightmost ``}`` backward and try ``json.loads`` until something
    parses. Returns ``None`` if no prefix parses cleanly.
    """

    if not text:
        return None
    for end in range(len(text), 0, -1):
        if text[end - 1] != "}":
            continue
        candidate = text[:end]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _heuristic_scoring(articles: list[dict[str, Any]]) -> EventScoring:
    """Fallback scoring when Haiku is unavailable.

    Returns a conservative EventScoring with ``rejection_reason`` set so
    the trigger endpoint can choose to reject or surface the failure.
    The summary falls back to the first article's title + summary.
    """

    if not articles:
        return EventScoring(
            event_quality_score=0.0,
            primary_category="other",
            sub_categories=[],
            key_entities=[],
            source_credibility=0.0,
            timeliness_score=0.0,
            raw_summary="",
            rejection_reason="empty article list",
        )
    top = articles[0]
    summary_bits: list[str] = []
    title = str(top.get("title") or "").strip()
    if title:
        summary_bits.append(title)
    blurb = str(top.get("summary") or "").strip()
    if blurb:
        summary_bits.append(blurb[:MAX_SUMMARY_CHARS])
    return EventScoring(
        event_quality_score=0.0,
        primary_category="other",
        sub_categories=[],
        key_entities=[],
        source_credibility=0.5,
        timeliness_score=0.5,
        raw_summary=" — ".join(summary_bits),
        rejection_reason="Haiku scoring unavailable (no API key / SDK / network)",
        model="heuristic_fallback",
    )


async def score_event_for_auction(
    articles: list[dict[str, Any]],
    anthropic_client: Any = None,
    *,
    api_key: Optional[str] = None,
    max_tokens: int = 1500,
    timeout: float = 30.0,
) -> EventScoring:
    """Score a news cluster on whether it's worth opening an auction.

    Returns event quality metadata only — does NOT produce a Polymarket
    question, candidate list, or selected index. Agents downstream of
    this call write their own questions.

    Parameters
    ----------
    articles:
        List of raw news dicts (``title``, ``summary``, ``source``,
        ``published``, ``url``). Capped at :data:`MAX_ARTICLES`.
    anthropic_client:
        Pre-constructed ``anthropic.Anthropic`` client. When ``None`` we
        try to construct one from ``api_key`` / ``ANTHROPIC_API_KEY``.
    api_key:
        Override for ``ANTHROPIC_API_KEY`` env var. Ignored when
        ``anthropic_client`` is provided.
    max_tokens / timeout:
        Forwarded to ``client.messages.create``.

    Returns
    -------
    EventScoring
        Always returns a valid dataclass; never raises. On failure,
        ``event_quality_score = 0.0`` and ``rejection_reason`` is set.
    """

    if not articles:
        LOGGER.debug("score_event_for_auction: empty articles list")
        return _heuristic_scoring(articles)

    client = anthropic_client
    if client is None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            LOGGER.info(
                "score_event_for_auction: ANTHROPIC_API_KEY not set — "
                "returning heuristic fallback"
            )
            return _heuristic_scoring(articles)
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError:
            LOGGER.warning(
                "score_event_for_auction: anthropic SDK not installed"
            )
            return _heuristic_scoring(articles)
        try:
            client = anthropic.Anthropic(api_key=key)
        except Exception as exc:  # pragma: no cover - SDK init shouldn't fail
            LOGGER.warning(
                "score_event_for_auction: client init failed: %s", exc
            )
            return _heuristic_scoring(articles)

    prompt = _build_scoring_prompt(articles)

    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=max_tokens,
            timeout=timeout,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # broad — SDK has many transient error types
        LOGGER.warning("score_event_for_auction: Haiku call failed: %s", exc)
        return _heuristic_scoring(articles)

    if not getattr(resp, "content", None):
        LOGGER.warning("score_event_for_auction: empty Haiku response")
        return _heuristic_scoring(articles)

    text = getattr(resp.content[0], "text", "") or ""
    text = _strip_fences(text)
    if not text:
        LOGGER.warning("score_event_for_auction: empty text in Haiku response")
        return _heuristic_scoring(articles)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        recovered = _try_parse_partial_json(text)
        if recovered is not None:
            LOGGER.info(
                "score_event_for_auction: recovered truncated Haiku output "
                "(%d chars)",
                len(text),
            )
            payload = recovered
        else:
            LOGGER.warning(
                "score_event_for_auction: Haiku returned non-JSON (%s): %s",
                exc,
                text[:300],
            )
            return _heuristic_scoring(articles)

    if not isinstance(payload, dict):
        LOGGER.warning(
            "score_event_for_auction: Haiku payload is not an object"
        )
        return _heuristic_scoring(articles)

    try:
        return _validate_scoring_payload(payload)
    except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
        LOGGER.warning(
            "score_event_for_auction: payload validation failed: %s", exc
        )
        return _heuristic_scoring(articles)


__all__ = [
    "EventScoring",
    "HAIKU_MODEL",
    "MIN_AUCTION_QUALITY",
    "score_event_for_auction",
]
