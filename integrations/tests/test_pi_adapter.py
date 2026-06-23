#!/usr/bin/env python3
"""Unit tests for the Pi Agent Unix-socket adapter's request handler.

The gateway round-trip is covered by test_roundtrip; here we test the adapter's
own contract with an injected fake backend:
  - store/query/health ops return structured dicts
  - unknown op -> error dict (never raises)
  - backend slower than the IPC timeout -> {"ok": False, "error": "timeout"},
    handler returns a dict (daemon stays alive) instead of crashing
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

_INTEG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_INTEG, "pi_agent"))
sys.path.insert(0, _INTEG)
from memory_adapter import handle_request  # noqa: E402


class FakeRouter:
    def __init__(self, slow: bool = False) -> None:
        self.slow = slow
        self.stored: list[str] = []

    def health(self) -> bool:
        return True

    def store(self, body, **kw) -> dict:
        if self.slow:
            time.sleep(0.6)
        self.stored.append(body)
        return {"ok": True, "id": "fake-1"}

    def query(self, text=None, **kw) -> list[dict]:
        return [{"id": "fake-1", "body": text or "", "scope": "x", "kind": "fact"}]


def _run(coro):
    return asyncio.run(coro)


def test_health_op() -> None:
    res = _run(handle_request({"op": "health"}, FakeRouter()))
    assert res["ok"] is True


def test_store_op() -> None:
    res = _run(handle_request({"op": "store", "body": "hi", "scope": "mesh-selftest"}, FakeRouter()))
    assert res["ok"] is True and res["id"] == "fake-1"


def test_store_requires_body() -> None:
    res = _run(handle_request({"op": "store"}, FakeRouter()))
    assert res["ok"] is False and "error" in res


def test_query_op() -> None:
    res = _run(handle_request({"op": "query", "text": "hi"}, FakeRouter()))
    assert res["ok"] is True and isinstance(res["results"], list) and res["results"]


def test_unknown_op_returns_error() -> None:
    res = _run(handle_request({"op": "bogus"}, FakeRouter()))
    assert res["ok"] is False and "error" in res


def test_timeout_returns_error_not_raise() -> None:
    res = _run(handle_request({"op": "store", "body": "x"}, FakeRouter(slow=True), timeout=0.2))
    assert res["ok"] is False and res.get("error") == "timeout"


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
