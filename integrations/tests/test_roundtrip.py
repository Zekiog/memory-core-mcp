#!/usr/bin/env python3
"""Live store -> query roundtrip test against the memory-mesh gateway.

Acceptance criterion (per client, from the integration spec): a stored
sentinel record must be retrievable via query. Runs against the real gateway
(ZMEMORY_GATEWAY_URL) using credentials from ~/.secrets.d/zmemory-mesh.env.

Self-cleaning: the sentinel is deleted afterwards via the `zmem` direct-ADB
tool (best-effort) so the shared store stays tidy. Runnable two ways:

    python3 integrations/tests/test_roundtrip.py     # prints PASS/FAIL
    pytest  integrations/tests/test_roundtrip.py      # if pytest installed
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mesh_router import MeshRouter  # noqa: E402


def _cleanup(mem_id: str | None) -> None:
    """Best-effort delete of the test record via the local zmem ADB tool."""
    if not mem_id:
        return
    zmem = os.path.expanduser("~/.claude/bin/zmem")
    if not os.path.exists(zmem):
        return
    try:
        subprocess.run([zmem, "rm", mem_id], timeout=25,
                       capture_output=True, check=False)
    except Exception:
        pass


def test_store_query_roundtrip() -> None:
    router = MeshRouter()
    assert router.health(), "gateway /healthz must be reachable"

    token = uuid.uuid4().hex
    sentinel = f"ZMESH-ROUNDTRIP {token} mesh self-test sentinel record"
    res = router.store(
        sentinel,
        title="zmesh roundtrip self-test",
        kind="capture",
        scope="mesh-selftest",
        source="mesh-roundtrip-test",
    )
    assert res.get("ok"), f"store failed: {res}"
    mem_id = res.get("id")
    assert mem_id, f"store returned no id: {res}"

    try:
        hits = router.query(f"ZMESH-ROUNDTRIP {token}", limit=5)
        ids = [h.get("id") for h in hits]
        bodies = " ".join(h.get("body", "") for h in hits)
        assert mem_id in ids or token in bodies, (
            f"stored sentinel not retrievable; got ids={ids}"
        )
    finally:
        _cleanup(mem_id)


if __name__ == "__main__":
    try:
        test_store_query_roundtrip()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - surface any error to the runner
        print(f"ERROR: {type(exc).__name__}: {exc}")
        sys.exit(2)
    print("PASS: store -> query roundtrip")
