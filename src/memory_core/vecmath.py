"""Pure vector math used to compare client- and in-DB-generated embeddings.

No DB, no I/O — exists so calibrate_embeddings.py (and its tests) can compare
two embedding vectors without round-tripping through Oracle's VECTOR_DISTANCE.
"""
from __future__ import annotations

import math
from typing import Sequence


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]. Raises ValueError on length mismatch."""
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
