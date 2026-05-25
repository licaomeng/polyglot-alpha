"""Extended few-shot exemplars for judges D1, D3, D4, D5, D6, D7, D8.

The shipped ``corpus/few_shots.json`` (50 rows) is entirely D2-flavored and
all rows are POSITIVE_EXAMPLE. The LLM-tier judges (D2/D3/D5/D6/D7) and the
LLM-fallback paths for the rule-based judges (D1, D4, D8) benefit from a
small per-dimension mix of POSITIVE / NEGATIVE / EDGE_CASE exemplars to
anchor the in-context-learning prompt.

This module defines that mix as a plain Python list. ``EXTENDED_EXEMPLARS``
is consumed by:

  * the DB ingest one-liner documented in the v2 corpus runbook (see
    README §5.31), which writes them as ``FewShotExemplar`` rows;
  * the LLM-tier D5 judge (``d5_resolution_clarity._build_llm_prompt``),
    which inlines the D5 entries into its system prompt;
  * the D1 LLM-fallback path (``d1_structural._build_llm_prompt``).

Each entry is a dict with keys ``dim``, ``role``, ``text``, ``rationale``.
Roles use the same string values as ``FewShotRole`` (POSITIVE_EXAMPLE,
NEGATIVE_EXAMPLE, EDGE_CASE) so they can be inserted directly.

Distribution (~10 per dimension x 7 dimensions = 71 rows):

  * D1 (structural)            : 5 POS + 5 NEG = 10
  * D3 (framing neutrality)    : 5 POS + 5 NEG = 10
  * D4 (granularity)           : 5 POS + 5 NEG = 10
  * D5 (resolution clarity)    : 5 POS + 5 NEG + 1 EDGE = 11
  * D6 (source reliability)    : 5 POS + 5 NEG = 10
  * D7 (leading check)         : 5 POS + 5 NEG = 10
  * D8 (duplicate detection)   : 5 POS + 5 NEG = 10
"""
from __future__ import annotations

from typing import Final

# --- Dimension constants (mirror the FewShotRole enum string values) ------- #
_POS: Final[str] = "POSITIVE_EXAMPLE"
_NEG: Final[str] = "NEGATIVE_EXAMPLE"
_EDGE: Final[str] = "EDGE_CASE"


EXTENDED_EXEMPLARS: list[dict[str, str]] = [
    # --------------------------------------------------------------------- #
    # D1: Structural conformance (P1-P6 canonical Polymarket templates)
    # --------------------------------------------------------------------- #
    {"dim": "D1", "role": _POS,
     "text": "Will Bitcoin exceed $100,000 by December 31, 2026?",
     "rationale": "P1 'Will X by [date]?' with explicit deadline and threshold."},
    {"dim": "D1", "role": _POS,
     "text": "Will the Federal Reserve cut interest rates by July 31, 2026?",
     "rationale": "P1 pattern with explicit cutoff date and binary policy outcome."},
    {"dim": "D1", "role": _POS,
     "text": "Will the S&P 500 close above 6,000 on December 31, 2026?",
     "rationale": "P3 asset-threshold framing with concrete numeric criterion."},
    {"dim": "D1", "role": _POS,
     "text": "Who will be the next CEO of OpenAI?",
     "rationale": "P4 'Who will be the next X?' open-set question."},
    {"dim": "D1", "role": _POS,
     "text": "How many hurricanes will form in the Atlantic by November 30, 2026?",
     "rationale": "P6 'How many X by [date]?' count-by-date framing."},

    {"dim": "D1", "role": _NEG,
     "text": "Bitcoin will probably go up",
     "rationale": "Declarative, not a question. No date, no resolution criteria."},
    {"dim": "D1", "role": _NEG,
     "text": "Thoughts on the Fed?",
     "rationale": "Open-ended, no binary outcome, no resolution rule."},
    {"dim": "D1", "role": _NEG,
     "text": "The economy is in trouble.",
     "rationale": "Assertion rather than predictive question. Fails P1-P6."},
    {"dim": "D1", "role": _NEG,
     "text": "Should I buy Tesla stock?",
     "rationale": "Advice question, not market-resolvable. No outcome rule."},
    {"dim": "D1", "role": _NEG,
     "text": "Will something happen?",
     "rationale": "Underspecified — no subject, no date, no resolution source."},

    # --------------------------------------------------------------------- #
    # D3: Framing neutrality
    # --------------------------------------------------------------------- #
    {"dim": "D3", "role": _POS,
     "text": "Will Donald Trump win the 2024 US presidential election?",
     "rationale": "Neutral framing, no loaded language, factual outcome."},
    {"dim": "D3", "role": _POS,
     "text": "Will Tesla deliver more than 2 million vehicles in 2026?",
     "rationale": "Quantitative threshold, neutral phrasing, no editorializing."},
    {"dim": "D3", "role": _POS,
     "text": "Will Apple release a foldable iPhone by December 31, 2026?",
     "rationale": "Neutral predictive framing, brand-mention is factual."},
    {"dim": "D3", "role": _POS,
     "text": "Will Ethereum's annualized staking yield exceed 5% by Q4 2026?",
     "rationale": "Technical, value-neutral. No appeals to emotion."},
    {"dim": "D3", "role": _POS,
     "text": "Will the WHO declare a new public health emergency by 2026-12-31?",
     "rationale": "Procedural, references an authoritative body, no spin."},

    {"dim": "D3", "role": _NEG,
     "text": "Will the corrupt politician steal the 2024 election?",
     "rationale": "Loaded terms 'corrupt' and 'steal' presuppose guilt and fraud."},
    {"dim": "D3", "role": _NEG,
     "text": "Will the disastrous Fed policy crash the economy by 2026?",
     "rationale": "'Disastrous' and 'crash' editorialize the underlying event."},
    {"dim": "D3", "role": _NEG,
     "text": "Will the woke mob cancel another celebrity by year-end?",
     "rationale": "'Woke mob' and 'cancel' are politically charged framing."},
    {"dim": "D3", "role": _NEG,
     "text": "Will the brilliant CEO save Twitter by 2026?",
     "rationale": "'Brilliant' and 'save' pre-judge the outcome and the actor."},
    {"dim": "D3", "role": _NEG,
     "text": "Will the failing economy finally collapse by 2026-12-31?",
     "rationale": "'Failing' and 'finally collapse' nudge toward YES."},

    # --------------------------------------------------------------------- #
    # D4: Granularity (single-event, not compound)
    # --------------------------------------------------------------------- #
    {"dim": "D4", "role": _POS,
     "text": "Will the PBoC cut the Reserve Requirement Ratio by 2026-12-31?",
     "rationale": "Single atomic event with clear resolution criterion."},
    {"dim": "D4", "role": _POS,
     "text": "Will SpaceX launch Starship to orbit by June 30, 2026?",
     "rationale": "One event, one actor, one outcome."},
    {"dim": "D4", "role": _POS,
     "text": "Will the EU AI Act take effect before August 1, 2026?",
     "rationale": "Single regulatory event with explicit deadline."},
    {"dim": "D4", "role": _POS,
     "text": "Will Argentina default on sovereign debt by Q4 2026?",
     "rationale": "Single financial event, defined timeframe."},
    {"dim": "D4", "role": _POS,
     "text": "Will OpenAI release GPT-5 by December 31, 2026?",
     "rationale": "One product event by one actor by one date."},

    {"dim": "D4", "role": _NEG,
     "text": "Will the Fed cut rates AND the S&P 500 hit 6,000 by 2026?",
     "rationale": "Compound 'AND' — should be split into two markets."},
    {"dim": "D4", "role": _NEG,
     "text": "Will China announce a rate cut, stimulus, and VAT extension by 2026?",
     "rationale": "Three independent events bundled together."},
    {"dim": "D4", "role": _NEG,
     "text": "Will Russia withdraw from Ukraine OR Putin step down by 2026?",
     "rationale": "Compound 'OR' creates ambiguous resolution paths."},
    {"dim": "D4", "role": _NEG,
     "text": "Will Apple release a foldable iPhone, AR glasses, and a car in 2026?",
     "rationale": "Three product launches bundled — split per device."},
    {"dim": "D4", "role": _NEG,
     "text": "Will Bitcoin hit $200K and Ethereum reach $10K by 2026-12-31?",
     "rationale": "Two independent asset thresholds combined — split into two."},

    # --------------------------------------------------------------------- #
    # D5: Resolution clarity (HIGHEST EV per README §5.22)
    # --------------------------------------------------------------------- #
    {"dim": "D5", "role": _POS,
     "text": ("Will the PBoC announce an RRR cut by 11:59 PM UTC on August 23, "
              "2026 per the official PBoC press release page (pbc.gov.cn)?"),
     "rationale": ("Explicit cutoff time, time zone, authoritative source URL, "
                   "and a YES/NO resolution rule.")},
    {"dim": "D5", "role": _POS,
     "text": ("Will the closing price of Bitcoin on Coinbase exceed $100,000 "
              "on December 31, 2026 at 23:59:59 UTC?"),
     "rationale": "Names exchange, asset, price, exact UTC cutoff."},
    {"dim": "D5", "role": _POS,
     "text": ("Will the Federal Reserve announce a rate decision change at "
              "its FOMC meeting scheduled for July 30, 2026, per the "
              "official FOMC statement?"),
     "rationale": "Authoritative source, exact event, explicit date."},
    {"dim": "D5", "role": _POS,
     "text": ("Will the Bureau of Labor Statistics report a non-farm payrolls "
              "number above 200,000 for July 2026 in its August 1, 2026 release?"),
     "rationale": "Specific report, specific number, specific release date."},
    {"dim": "D5", "role": _POS,
     "text": ("Will Tesla's Q4 2026 earnings press release, published by "
              "January 31, 2027 at 9:00 PM ET, report >$30B in revenue?"),
     "rationale": "Press release, deadline, threshold, all explicit."},

    {"dim": "D5", "role": _NEG,
     "text": "Will the PBoC do something monetary soon?",
     "rationale": "'Something' and 'soon' are unbound. No source, no cutoff."},
    {"dim": "D5", "role": _NEG,
     "text": "Will Bitcoin do well this year?",
     "rationale": "'Do well' is undefined; no specific threshold or source."},
    {"dim": "D5", "role": _NEG,
     "text": "Will there be a big tech deal in 2026?",
     "rationale": "'Big' is subjective; no actors, no specific deal, no source."},
    {"dim": "D5", "role": _NEG,
     "text": "Will the economy improve by next year?",
     "rationale": "'Improve' is vague; no metric, no cutoff, no source."},
    {"dim": "D5", "role": _NEG,
     "text": "Will there be a recession?",
     "rationale": "No date, no defining body (NBER?), no source URL."},

    {"dim": "D5", "role": _EDGE,
     "text": ("Will the United States sign a minerals deal with Ukraine by "
              "March 31, 2025?"),
     "rationale": ("Historical UMA dispute case (~$7M payout reversed). "
                   "D5 should flag missing: who signs (Trump? State Dept?), "
                   "what counts as a 'deal' (MOU vs treaty), and the source "
                   "of record. Ambiguity here cost the market.")},

    # --------------------------------------------------------------------- #
    # D6: Source reliability
    # --------------------------------------------------------------------- #
    {"dim": "D6", "role": _POS,
     "text": ("Will the SEC approve a Solana ETF by 2026-12-31 per a Form "
              "19b-4 order on sec.gov?"),
     "rationale": ".gov source, specific SEC filing type. Authoritative."},
    {"dim": "D6", "role": _POS,
     "text": ("Will the BLS report unemployment above 5% for August 2026 "
              "(bls.gov)?"),
     "rationale": "Official US statistical agency cited."},
    {"dim": "D6", "role": _POS,
     "text": ("Will the IMF lower its 2026 global growth forecast in its "
              "October 2026 World Economic Outlook (imf.org)?"),
     "rationale": "International authoritative source for the metric."},
    {"dim": "D6", "role": _POS,
     "text": ("Will the WHO declare a Public Health Emergency of International "
              "Concern by 2026-12-31 (who.int)?"),
     "rationale": "Procedural declaration, authoritative source."},
    {"dim": "D6", "role": _POS,
     "text": ("Will the Federal Reserve's H.15 release show the 10-year "
              "Treasury yield above 5% on 2026-09-30 (federalreserve.gov)?"),
     "rationale": "Official Fed data series with explicit identifier."},

    {"dim": "D6", "role": _NEG,
     "text": ("Will Bitcoin reach $200K by 2026 per some-random-blog.example.com?"),
     "rationale": "Unverified blog source for a price claim."},
    {"dim": "D6", "role": _NEG,
     "text": ("Will Trump be re-elected per a leaked Telegram channel?"),
     "rationale": "Anonymous unverifiable source. Unsafe for resolution."},
    {"dim": "D6", "role": _NEG,
     "text": "Will the moon landing be exposed as fake by 2026 per redditors?",
     "rationale": "Forum opinions are not authoritative for any claim."},
    {"dim": "D6", "role": _NEG,
     "text": ("Will GDP grow >3% by 2026 per a tweet from @anon_economist?"),
     "rationale": "Anonymous social-media source. No accountability."},
    {"dim": "D6", "role": _NEG,
     "text": ("Will the iPhone 18 ship by 2026 per a YouTube leaker?"),
     "rationale": "Unverified leaker, not Apple itself; no press release."},

    # --------------------------------------------------------------------- #
    # D7: Leading-language check
    # --------------------------------------------------------------------- #
    {"dim": "D7", "role": _POS,
     "text": "Will the Federal Reserve cut interest rates by July 31, 2026?",
     "rationale": "Plain interrogative form. No bias language."},
    {"dim": "D7", "role": _POS,
     "text": "Will Bitcoin's price exceed $100,000 on December 31, 2026?",
     "rationale": "Factual, no qualifiers like 'obviously' or 'finally'."},
    {"dim": "D7", "role": _POS,
     "text": "Will Tesla report deliveries above 500,000 units in Q3 2026?",
     "rationale": "Neutral threshold question, no nudging adverbs."},
    {"dim": "D7", "role": _POS,
     "text": "Will the EU ratify the AI Act amendments by Q2 2026?",
     "rationale": "Procedural language, no presupposition."},
    {"dim": "D7", "role": _POS,
     "text": "Will SpaceX achieve a Starship orbital reuse by 2026-12-31?",
     "rationale": "Technical milestone, no editorial framing."},

    {"dim": "D7", "role": _NEG,
     "text": "Will the Fed obviously cut rates by 2026-12-31?",
     "rationale": "'Obviously' presupposes the answer is YES."},
    {"dim": "D7", "role": _NEG,
     "text": "Will Bitcoin finally crash by 2026?",
     "rationale": "'Finally' implies an inevitable, awaited outcome."},
    {"dim": "D7", "role": _NEG,
     "text": "Will the doomed company go bankrupt by 2026?",
     "rationale": "'Doomed' nudges traders toward YES bankruptcy outcome."},
    {"dim": "D7", "role": _NEG,
     "text": "Will the inevitable rate hike happen by 2026?",
     "rationale": "'Inevitable' is a leading qualifier."},
    {"dim": "D7", "role": _NEG,
     "text": "Will the obvious winner triumph in the 2026 election?",
     "rationale": "'Obvious winner' + 'triumph' both lean YES."},

    # --------------------------------------------------------------------- #
    # D8: Duplicate detection (semantic near-duplicates of corpus markets)
    # --------------------------------------------------------------------- #
    {"dim": "D8", "role": _POS,
     "text": ("Will the People's Bank of China announce a Reserve Requirement"
              " Ratio cut before August 23, 2026?"),
     "rationale": "Novel topic and phrasing relative to the shipped corpus."},
    {"dim": "D8", "role": _POS,
     "text": "Will Argentina's central bank devalue the peso by Q1 2026?",
     "rationale": "Specific actor + action + timeframe not in T5 corpus."},
    {"dim": "D8", "role": _POS,
     "text": "Will the EU AI Act enforcement start before 2026-08-01?",
     "rationale": "Specific regulation start-date question."},
    {"dim": "D8", "role": _POS,
     "text": ("Will Japan's GPIF allocate more than 5% to crypto assets by "
              "2026-12-31?"),
     "rationale": "Atypical actor + asset class combo; novel."},
    {"dim": "D8", "role": _POS,
     "text": ("Will the Bank of England raise the bank rate to above 6% by "
              "Q3 2026?"),
     "rationale": "Distinct from existing corpus Fed/PBoC questions."},

    {"dim": "D8", "role": _NEG,
     "text": "Will Bitcoin exceed $100,000 by December 31, 2026?",
     "rationale": "Near-identical to many shipped corpus titles — duplicate."},
    {"dim": "D8", "role": _NEG,
     "text": "Will Bitcoin go above $100k by end of 2026?",
     "rationale": "Paraphrase of a high-volume corpus market; D8 should flag."},
    {"dim": "D8", "role": _NEG,
     "text": "MicroStrategy sells any Bitcoin by December 31, 2026?",
     "rationale": "Verbatim string from corpus index_meta.json idx=1."},
    {"dim": "D8", "role": _NEG,
     "text": "Will Trump win the 2024 presidential election?",
     "rationale": "Canonical resolved market, would semantically collide."},
    {"dim": "D8", "role": _NEG,
     "text": "Will Bitcoin reach 100,000 dollars before 2027?",
     "rationale": "Synonym paraphrase of the BTC-100k corpus market."},
]


def get_exemplars_for_dimension(dim: str) -> list[dict[str, str]]:
    """Return all exemplars whose ``dim`` matches the given dimension code.

    Used by the LLM-tier judges (D1 fallback, D5 LLM) to build their
    in-context exemplar block.
    """

    return [ex for ex in EXTENDED_EXEMPLARS if ex["dim"] == dim]
