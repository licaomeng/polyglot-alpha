"""Style-alignment judges D1-D8.

D1, D4, D5 are deterministic / regex-driven.
D2, D3, D6, D7 share a single LLM call via :func:`run_style_llm_batch`.
D8 uses sentence-transformers + FAISS for semantic duplicate detection.
"""

from polyglot_alpha.judges.style_alignment.d1_structural import judge_d1_structural
from polyglot_alpha.judges.style_alignment.d2_stylistic import judge_d2_stylistic
from polyglot_alpha.judges.style_alignment.d3_framing import judge_d3_framing
from polyglot_alpha.judges.style_alignment.d4_granularity import judge_d4_granularity
from polyglot_alpha.judges.style_alignment.d5_resolution_clarity import (
    judge_d5_resolution_clarity,
)
from polyglot_alpha.judges.style_alignment.d6_source_reliability import (
    judge_d6_source_reliability,
)
from polyglot_alpha.judges.style_alignment.d7_leading_check import (
    judge_d7_leading_check,
)
from polyglot_alpha.judges.style_alignment.d8_duplicate_detection import (
    judge_d8_duplicate_detection,
)
from polyglot_alpha.judges.style_alignment.llm_batch import run_style_llm_batch

__all__ = [
    "judge_d1_structural",
    "judge_d2_stylistic",
    "judge_d3_framing",
    "judge_d4_granularity",
    "judge_d5_resolution_clarity",
    "judge_d6_source_reliability",
    "judge_d7_leading_check",
    "judge_d8_duplicate_detection",
    "run_style_llm_batch",
]
