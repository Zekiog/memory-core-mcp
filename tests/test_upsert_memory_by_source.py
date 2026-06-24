#!/usr/bin/env python3
"""Live integration test for db.upsert_memory_by_source against zmemory-adb.

Follows the same convention as integrations/tests/test_roundtrip.py: hits the
real DB using the local .env, self-cleans the rows it creates.
"""
from __future__ import annotations

import sys
import uuid

from memory_core import db
from memory_core.config import Config

cfg = Config.load()
db.init_pool(cfg)


def _cleanup(mem_id: str) -> None:
    with db.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM memories WHERE id = :id", id=mem_id)
        c.commit()


def test_first_call_inserts() -> None:
    source = f"test:upsert:{uuid.uuid4().hex}"
    mem_id = None
    try:
        mem_id, created = db.upsert_memory_by_source(
            cfg, scope="test-upsert", source=source, title="v1",
            body="first version", kind="reference", tags=None,
        )
        assert created is True
        row = db.get(cfg, mem_id)
        assert row["title"] == "v1"
        assert row["body"] == "first version"
    finally:
        if mem_id:
            _cleanup(mem_id)


def test_second_call_with_same_source_updates_in_place() -> None:
    source = f"test:upsert:{uuid.uuid4().hex}"
    mem_id = None
    try:
        first_id, first_created = db.upsert_memory_by_source(
            cfg, scope="test-upsert", source=source, title="v1",
            body="first version", kind="reference", tags=None,
        )
        second_id, second_created = db.upsert_memory_by_source(
            cfg, scope="test-upsert", source=source, title="v2",
            body="second version", kind="reference", tags=None,
        )
        assert first_created is True
        assert second_created is False
        assert first_id == second_id
        row = db.get(cfg, second_id)
        assert row["title"] == "v2"
        assert row["body"] == "second version"

        with db.conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM memories WHERE scope = :s AND source = :src",
                s="test-upsert", src=source,
            )
            (count,) = cur.fetchone()
        assert count == 1, f"expected exactly 1 row, found {count}"
        mem_id = second_id
    finally:
        if mem_id:
            _cleanup(mem_id)


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
