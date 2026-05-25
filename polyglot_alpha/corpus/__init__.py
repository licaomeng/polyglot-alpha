"""Polymarket ground-truth corpus package.

Submodules:
    scraper            — Gamma API crawl (open + closed) -> parquet
    resolved_scraper   — Gamma API crawl of resolved markets -> parquet/csv/jsonl
    resolved_analysis  — statistics + summary writer for the resolved corpus
    embed              — MiniLM embeddings + FAISS index build
    lookup             — k-NN search over the FAISS index
    pattern_analysis   — regex framing-pattern classifier + report
    few_shots          — diverse high-volume exemplar picker
    style_guide        — LLM-distilled style write-up
"""

from polyglot_alpha.corpus.lookup import (  # noqa: F401
    Lookup,
    SimilarHit,
    find_similar,
)
from polyglot_alpha.corpus.pattern_analysis import (  # noqa: F401
    PATTERN_LABELS,
    PatternStats,
    classify_pattern,
    summarize_patterns,
)
