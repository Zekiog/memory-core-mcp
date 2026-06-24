#!/usr/bin/env python3
"""Unit tests for readyz.readyz_payload — pure mapping, no DB, no server."""
from __future__ import annotations

import sys

from memory_core.readyz import readyz_payload


def test_indb_mode_is_ready() -> None:
    body, status = readyz_payload("indb")
    assert status == 200
    assert body == {"ok": True, "embed_mode": "indb"}


def test_client_mode_is_ready() -> None:
    body, status = readyz_payload("client")
    assert status == 200
    assert body == {"ok": True, "embed_mode": "client"}


def test_none_mode_is_not_ready() -> None:
    body, status = readyz_payload("none")
    assert status == 503
    assert body == {"ok": False, "embed_mode": "none"}


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
