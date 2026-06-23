#!/usr/bin/env python3
"""Pi Agent memory adapter — an async Unix-socket daemon bridging local
agents (the TypeScript "Pi Army", or anything) to the shared memory mesh.

Any local process can store/recall shared memory by writing one JSON line to
the socket and reading one JSON line back, without embedding gateway URLs or
bearer tokens itself — the daemon owns the single MeshRouter.

Wire protocol (newline-delimited JSON, one request per connection):
    -> {"op": "health"}
    <- {"ok": true}
    -> {"op": "store", "body": "...", "title"?, "kind"?, "scope"?, "source"?}
    <- {"ok": true, "id": "..."}            (or {"ok": false, "degraded": true})
    -> {"op": "query", "text": "...", "scope"?, "kind"?, "limit"?}
    <- {"ok": true, "results": [...]}

Resilience contract:
  - A backend call slower than ZMEMORY_IPC_TIMEOUT (default 15s) yields
    {"ok": false, "error": "timeout"} and the daemon keeps serving.
  - Any handler error becomes an error dict; the server never crashes on a
    bad/oversized/garbage request.

Run:  python3 memory_adapter.py        (managed by launchd in production)
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys

# Allow `import mesh_router` whether run from the repo or installed standalone.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mesh_router import MeshRouter  # noqa: E402

SOCKET_PATH = os.environ.get(
    "ZMEMORY_SOCKET", os.path.expanduser("~/.pi/run/zmemory-mesh.sock")
)
IPC_TIMEOUT = float(os.environ.get("ZMEMORY_IPC_TIMEOUT", "15"))
MAX_REQUEST_BYTES = 256 * 1024


async def handle_request(req: dict, router, timeout: float = IPC_TIMEOUT) -> dict:
    """Map one request dict to a response dict. Never raises."""
    op = (req or {}).get("op")
    loop = asyncio.get_running_loop()
    try:
        if op == "health":
            ok = await asyncio.wait_for(
                loop.run_in_executor(None, router.health), timeout
            )
            return {"ok": bool(ok)}

        if op == "store":
            body = req.get("body")
            if not body:
                return {"ok": False, "error": "body required"}

            def _store():
                return router.store(
                    body, title=req.get("title"), kind=req.get("kind", "fact"),
                    scope=req.get("scope"), source=req.get("source", "pi-agent"),
                )

            res = await asyncio.wait_for(loop.run_in_executor(None, _store), timeout)
            out = {"ok": bool(res.get("ok")), "id": res.get("id")}
            if res.get("degraded"):
                out["degraded"] = True
            return out

        if op == "query":
            def _query():
                return router.query(
                    req.get("text"), scope=req.get("scope"),
                    kind=req.get("kind"), limit=int(req.get("limit", 10)),
                )

            rows = await asyncio.wait_for(loop.run_in_executor(None, _query), timeout)
            return {"ok": True, "results": rows}

        return {"ok": False, "error": f"unknown op: {op!r}"}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout", "degraded": True}
    except Exception as exc:  # noqa: BLE001 - daemon must never die on one request
        return {"ok": False, "error": str(exc)}


async def _serve_client(reader, writer, router, timeout) -> None:
    try:
        raw = await reader.readline()
        if len(raw) > MAX_REQUEST_BYTES:
            resp = {"ok": False, "error": "request too large"}
        else:
            try:
                req = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                req = {}
            resp = await handle_request(req, router, timeout)
    except Exception as exc:  # noqa: BLE001
        resp = {"ok": False, "error": str(exc)}
    try:
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def serve(socket_path: str = SOCKET_PATH, router=None,
                timeout: float = IPC_TIMEOUT) -> None:
    router = router or MeshRouter()
    os.makedirs(os.path.dirname(socket_path), exist_ok=True)
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    server = await asyncio.start_unix_server(
        lambda r, w: _serve_client(r, w, router, timeout), path=socket_path
    )
    os.chmod(socket_path, 0o600)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    sys.stderr.write(f"[memory_adapter] listening on {socket_path} "
                     f"(gateway={router.gateway_url or 'UNSET'}, timeout={timeout}s)\n")
    sys.stderr.flush()
    async with server:
        await stop.wait()
    if os.path.exists(socket_path):
        os.unlink(socket_path)


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
