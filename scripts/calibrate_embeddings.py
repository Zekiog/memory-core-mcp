#!/usr/bin/env python3
"""Compare client (fastembed L6) vs in-DB (Oracle ONNX L6) embeddings on a
fixed sample of representative texts. If they're numerically interchangeable
(cosine >= 0.999 for every sample), the 191 already-stored client vectors
stay valid once in-DB becomes the primary writer -- no backfill needed.

Run after db/03_embedding_model.sql has been loaded (Task 6) and ORA-40284
no longer occurs.

Usage: ./.venv/bin/python scripts/calibrate_embeddings.py
Exit code: 0 if min cosine >= THRESHOLD, 1 otherwise (recommends backfill).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memory_core import db, embedder  # noqa: E402
from memory_core.config import Config  # noqa: E402
from memory_core.vecmath import cosine_similarity  # noqa: E402

THRESHOLD = 0.999

SAMPLES = [
    "Hermes gateway restart loop investigation",
    "trading workflow daily digest for n8n",
    "memory-core embedding degradation alarm",
    "OpenClaw could not reach the semantic index",
    "Oracle ADB vector search calibration",
    "n8n workflow catalog sync to memory-core",
    "fastembed cache directory under ProtectHome",
    "cosine similarity between client and in-DB vectors",
    "avm-02 systemd drop-in override",
    "WireGuard mesh health check on z-mesh",
]


def _indb_embed(cfg: Config, text: str) -> list[float]:
    with db.conn() as c, c.cursor() as cur:
        cur.execute(
            f"SELECT VECTOR_EMBEDDING({cfg.embed_model} USING :t AS data) FROM dual",
            t=text,
        )
        (vec,) = cur.fetchone()
        return list(vec)


def main() -> int:
    cfg = Config.load()
    db.init_pool(cfg)

    if not embedder.available():
        print("FAIL: client embedder unavailable locally -- cannot calibrate")
        return 1

    cosines = []
    for text in SAMPLES:
        client_vec = list(embedder.embed(text))
        try:
            indb_vec = _indb_embed(cfg, text)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: in-DB embedding failed for {text!r}: {exc}")
            return 1
        sim = cosine_similarity(client_vec, indb_vec)
        cosines.append(sim)
        print(f"{sim:.6f}  {text!r}")

    min_sim = min(cosines)
    avg_sim = sum(cosines) / len(cosines)
    print(f"\nmin={min_sim:.6f} avg={avg_sim:.6f} threshold={THRESHOLD}")
    if min_sim >= THRESHOLD:
        print("RESULT: interchangeable -- no backfill needed, in-DB can become primary")
        return 0
    print("RESULT: drift detected -- run db.backfill_embeddings() against in-DB "
          "after switching embed_mode priority, to make all rows consistent")
    return 1


if __name__ == "__main__":
    sys.exit(main())
