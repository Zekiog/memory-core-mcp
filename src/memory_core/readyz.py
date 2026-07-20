"""Pure readiness-status mapping for the /readyz HTTP probe.

Kept separate from server.py (which has import-time side effects: Config.load()
and db.init_pool()) so this mapping is unit-testable with zero DB dependency.
"""
from __future__ import annotations


def readyz_payload(embed_mode: str) -> tuple[dict, int]:
    """Map an embed_mode ('indb' | 'client' | 'none') to (json_body, http_status).

    Ready (200) when any embedding path is active; degraded (503) when search
    has silently fallen back to keyword-only matching.
    """
    ok = embed_mode != "none"
    return {"ok": ok, "embed_mode": embed_mode}, (200 if ok else 503)
