"""Tests for ``polyglot_alpha.corpus.few_shots_extended.EXTENDED_EXEMPLARS``.

The shipped 50-row ``corpus/few_shots.json`` covered only D2/POSITIVE.
``EXTENDED_EXEMPLARS`` ships ~10 POS+NEG exemplars per judge dimension
(D1, D3, D4, D5, D6, D7, D8) plus at least one EDGE_CASE entry for D5.
These tests guard the count, dimension coverage, role distribution,
de-duplication, and the per-row contract.
"""

from __future__ import annotations

from collections import Counter

import pytest

from polyglot_alpha.corpus.few_shots_extended import (
    EXTENDED_EXEMPLARS,
    get_exemplars_for_dimension,
)

# The full set of dimensions that ``EXTENDED_EXEMPLARS`` is expected to cover.
EXPECTED_DIMENSIONS: tuple[str, ...] = ("D1", "D3", "D4", "D5", "D6", "D7", "D8")
MIN_PER_DIMENSION: int = 10  # 5 POS + 5 NEG
MIN_TOTAL: int = 70  # 7 dimensions * 10 baseline


def test_extended_exemplars_meets_min_count() -> None:
    """At least 70 rows across all dimensions (per Fix 2 spec)."""

    assert len(EXTENDED_EXEMPLARS) >= MIN_TOTAL, (
        f"expected >= {MIN_TOTAL} extended exemplars, got {len(EXTENDED_EXEMPLARS)}"
    )


def test_extended_exemplars_per_dimension_count() -> None:
    """Each of the 7 dimensions must have at least 10 exemplars."""

    per_dim = Counter(ex["dim"] for ex in EXTENDED_EXEMPLARS)
    for dim in EXPECTED_DIMENSIONS:
        assert per_dim[dim] >= MIN_PER_DIMENSION, (
            f"dim {dim} has only {per_dim[dim]} exemplars; need >= "
            f"{MIN_PER_DIMENSION}"
        )


def test_extended_exemplars_role_distribution() -> None:
    """Every covered dimension must include POSITIVE and NEGATIVE rows."""

    by_dim: dict[str, set[str]] = {}
    for ex in EXTENDED_EXEMPLARS:
        by_dim.setdefault(ex["dim"], set()).add(ex["role"])

    for dim in EXPECTED_DIMENSIONS:
        roles = by_dim.get(dim, set())
        assert "POSITIVE_EXAMPLE" in roles, f"{dim} missing POSITIVE_EXAMPLE rows"
        assert "NEGATIVE_EXAMPLE" in roles, f"{dim} missing NEGATIVE_EXAMPLE rows"

    # D5 has the highest EV per README §5.22 — it must carry at least one
    # EDGE_CASE example (the UMA dispute reference).
    assert "EDGE_CASE" in by_dim.get("D5", set()), (
        "D5 must include an EDGE_CASE exemplar (e.g. UMA dispute reference)"
    )


def test_extended_exemplars_no_duplicates() -> None:
    """A (dim, role, text) tuple must not repeat."""

    keys = [(ex["dim"], ex["role"], ex["text"]) for ex in EXTENDED_EXEMPLARS]
    duplicates = [item for item, n in Counter(keys).items() if n > 1]
    assert not duplicates, f"duplicate (dim,role,text) entries: {duplicates}"


def test_extended_exemplars_every_row_has_rationale() -> None:
    """Each exemplar must have a non-empty ``rationale`` and valid keys."""

    required_keys = {"dim", "role", "text", "rationale"}
    for i, ex in enumerate(EXTENDED_EXEMPLARS):
        assert required_keys.issubset(ex.keys()), (
            f"row {i} missing keys: {required_keys - set(ex.keys())}"
        )
        assert ex["text"].strip(), f"row {i} has empty text"
        assert ex["rationale"].strip(), f"row {i} has empty rationale"
        assert ex["dim"] in EXPECTED_DIMENSIONS, (
            f"row {i} has unexpected dim {ex['dim']!r}"
        )
        assert ex["role"] in {
            "POSITIVE_EXAMPLE",
            "NEGATIVE_EXAMPLE",
            "EDGE_CASE",
        }, f"row {i} has unexpected role {ex['role']!r}"


@pytest.mark.parametrize("dim", EXPECTED_DIMENSIONS)
def test_get_exemplars_for_dimension_filter(dim: str) -> None:
    """``get_exemplars_for_dimension`` returns only matching rows."""

    rows = get_exemplars_for_dimension(dim)
    assert rows, f"no exemplars returned for {dim}"
    assert all(r["dim"] == dim for r in rows)
