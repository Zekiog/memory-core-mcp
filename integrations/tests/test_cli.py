#!/usr/bin/env python3
"""CLI-surface tests for mesh_router (the entrypoint the shell hook drives).

  - query --inject with hits   -> emits a delimited MEMORY-MESH block, exit 0
  - query --inject when empty  -> emits nothing, exit 0 (original prompt as-is)
  - store                      -> prints JSON with an id (then cleaned up)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTER = os.path.join(os.path.dirname(HERE), "mesh_router.py")


def _run(args, env_extra=None, timeout=40):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, ROUTER, *args],
        capture_output=True, text=True, env=env, timeout=timeout,
    )


def _cleanup(mem_id):
    zmem = os.path.expanduser("~/.claude/bin/zmem")
    if mem_id and os.path.exists(zmem):
        subprocess.run([zmem, "rm", mem_id], capture_output=True, check=False, timeout=25)


def test_inject_skips_when_empty() -> None:
    # Unreachable gateway -> degraded -> [] -> no injection, clean exit.
    p = _run(["query", "ZMESH-NOPE", "--inject"],
             {"ZMEMORY_GATEWAY_URL": "http://127.0.0.1:1", "ZMEMORY_BEARER_TOKEN": "x"})
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "", f"expected no injection, got: {p.stdout!r}"


def test_inject_emits_block_when_hits() -> None:
    token = uuid.uuid4().hex
    seed = _run(["store", f"ZMESH-CLI {token} injection fixture",
                 "--scope", "mesh-selftest", "--source", "mesh-cli-test",
                 "--kind", "capture"])
    assert seed.returncode == 0, seed.stderr
    mem_id = json.loads(seed.stdout).get("id")
    try:
        p = _run(["query", f"ZMESH-CLI {token}", "--inject", "--limit", "5"])
        assert p.returncode == 0, p.stderr
        assert "MEMORY-MESH" in p.stdout, f"missing block marker: {p.stdout!r}"
        assert token in p.stdout, "stored fixture not present in injection"
    finally:
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
