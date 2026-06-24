#!/usr/bin/env python3
"""Unit tests for vecmath.cosine_similarity — pure math, no DB, no I/O."""
from __future__ import annotations

import sys

from memory_core.vecmath import cosine_similarity


def test_identical_vectors_have_similarity_one() -> None:
    v = [1.0, 2.0, 3.0]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-9


def test_orthogonal_vectors_have_similarity_zero() -> None:
    assert abs(cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_opposite_vectors_have_similarity_negative_one() -> None:
    assert abs(cosine_similarity([1.0, 0.0], [-1.0, 0.0]) - (-1.0)) < 1e-9


def test_scale_invariant() -> None:
    a = [3.0, 4.0]
    b = [6.0, 8.0]  # same direction, different magnitude
    assert abs(cosine_similarity(a, b) - 1.0) < 1e-9


def test_mismatched_length_raises() -> None:
    try:
        cosine_similarity([1.0, 2.0], [1.0])
    except ValueError:
        return
    raise AssertionError("expected ValueError for mismatched vector lengths")


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as exc:
                print(f"FAIL: {name}: {exc}")
                failures.append(name)
            except Exception as exc:  # noqa: BLE001
                print(f"ERROR: {name}: {type(exc).__name__}: {exc}")
                failures.append(name)
    sys.exit(1 if failures else 0)
