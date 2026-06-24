"""Client-side embedding backend (fastembed / ONNX, no torch, local-first).

Used when the in-DB ONNX model is not loaded. Produces 384-dim vectors with
sentence-transformers/all-MiniLM-L6-v2, matching the VECTOR(384) column.
"""
from __future__ import annotations

import array
import os

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_DIM = 384
_model = None
_unavailable = False


def available() -> bool:
    """True if fastembed loads. Lazy + cached; never raises."""
    global _model, _unavailable
    if _model is not None:
        return True
    if _unavailable:
        return False
    try:
        from fastembed import TextEmbedding

        cache_dir = os.environ.get("FASTEMBED_CACHE_DIR") or None
        _model = TextEmbedding(model_name=_MODEL_NAME, cache_dir=cache_dir)
        return True
    except Exception:  # noqa: BLE001 - degrade gracefully to keyword search
        _unavailable = True
        return False


def embed(text: str) -> array.array | None:
    """Return a 384-dim float32 vector, or None if the backend is unavailable."""
    if not available():
        return None
    vec = next(iter(_model.embed([text])))
    return array.array("f", (float(x) for x in vec))


def dim() -> int:
    return _DIM
