#!/usr/bin/env python3
"""Edge-case tests: the router must degrade gracefully, never crash a caller.

Spec contract:
  - Gateway unreachable -> memory-less mode, no exception thrown.
      store() -> {"ok": False, "degraded": True, ...}
      query() -> []   (caller then skips context injection)
      health() -> False
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mesh_router import MeshRouter  # noqa: E402


def _dead_router() -> MeshRouter:
    # 127.0.0.1:1 -> connection refused immediately; short timeout as backstop.
    return MeshRouter(gateway_url="http://127.0.0.1:1", bearer="x", timeout=2.0)


def test_health_false_when_unreachable() -> None:
    assert _dead_router().health() is False


def test_store_degrades_without_raising() -> None:
    res = _dead_router().store("must not raise", scope="mesh-selftest")
    assert res.get("ok") is False
    assert res.get("degraded") is True


def test_query_returns_empty_without_raising() -> None:
    assert _dead_router().query("anything") == []


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
