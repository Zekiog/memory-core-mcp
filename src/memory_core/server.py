"""memory-core MCP server.

Exposes the Oracle ADB 23ai knowledge-data-memory store as MCP tools.
Transport: stdio by default; set MCP_HTTP=1 for streamable-http (multi-access).
"""
from __future__ import annotations

import hmac
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import db
from .config import Config

cfg = Config.load()
db.init_pool(cfg)

mcp = FastMCP(
    "memory-core",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8848")),
)


@mcp.tool()
def memory_add(
    body: str,
    title: str | None = None,
    kind: str = "fact",
    scope: str | None = None,
    tags: dict[str, Any] | None = None,
    source: str = "mcp",
    links: list[str] | None = None,
) -> dict:
    """Persist a memory. kind: capture|decision|fact|reference|project.

    Returns the new memory id. Optionally backlink to existing memory ids
    via `links` (relation 'relates_to').
    """
    mem_id = db.add_memory(
        cfg, body=body, title=title, kind=kind,
        scope=scope or cfg.default_scope, tags=tags, source=source,
        vault_path=None,
    )
    for dst in links or []:
        db.link(cfg, src_id=mem_id, dst_id=dst, rel="relates_to")
    return {"id": mem_id, "kind": kind, "scope": scope or cfg.default_scope}


@mcp.tool()
def memory_search(
    query: str,
    scope: str | None = None,
    kind: str | None = None,
    limit: int = 8,
) -> list[dict]:
    """Semantic search across memories (cosine over in-DB embeddings).

    Falls back to keyword match if the embedding model is not yet loaded.
    """
    return db.search(cfg, query=query, scope=scope, kind=kind,
                      limit=max(1, min(limit, 50)))


@mcp.tool()
def memory_get(id: str) -> dict | None:
    """Fetch one memory by id, including its outgoing graph links."""
    return db.get(cfg, id)


@mcp.tool()
def memory_recent(
    scope: str | None = None,
    kind: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """List most-recently updated memories (optionally filtered)."""
    return db.recent(cfg, scope=scope, kind=kind, limit=max(1, min(limit, 100)))


@mcp.tool()
def memory_link(src_id: str, dst_id: str, rel: str = "relates_to") -> dict:
    """Create a typed graph edge between two memories (idempotent)."""
    db.link(cfg, src_id=src_id, dst_id=dst_id, rel=rel)
    return {"src_id": src_id, "dst_id": dst_id, "rel": rel}


@mcp.tool()
def memory_stats() -> dict:
    """Counts by scope/kind and whether semantic search is active."""
    return db.stats(cfg)


class _BearerASGI:
    """Pure-ASGI bearer-token gate. SSE-safe: it never wraps the response
    stream (unlike BaseHTTPMiddleware), so MCP streamable-http keeps working.

    Enforced only when MEMORY_BEARER is set. `/healthz` stays open for probes.
    """

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        if scope.get("path") == "/healthz":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"ok":true}'})
            return
        if scope.get("path") == "/readyz":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        if not hmac.compare_digest(token, self._token):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body",
                        "body": b'{"error":"unauthorized"}'})
            return
        await self._app(scope, receive, send)


async def _http_ingest(request: Any) -> Any:
    """REST: POST /ingest — single-shot memory write for automation (n8n)."""
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    body = (data or {}).get("body")
    if not body:
        return JSONResponse({"error": "body is required"}, status_code=400)

    def _write() -> str:
        mid = db.add_memory(
            cfg, body=body, title=data.get("title"),
            kind=data.get("kind", "fact"),
            scope=data.get("scope") or cfg.default_scope,
            tags=data.get("tags"), source=data.get("source", "n8n"),
            vault_path=data.get("vault_path"),
        )
        for dst in data.get("links") or []:
            try:
                db.link(cfg, src_id=mid, dst_id=dst, rel="relates_to")
            except Exception:
                pass
        return mid

    mid = await run_in_threadpool(_write)
    return JSONResponse({"id": mid, "ok": True})


async def _http_catalog_upsert(request: Any) -> Any:
    """REST: POST /catalog/upsert — idempotent upsert keyed by (scope, source).

    Used by content-sync automations (n8n workflow catalog, Task 12) so
    re-running a sync updates existing rows instead of duplicating them.
    Requires bearer auth, same as /ingest.
    """
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    body = (data or {}).get("body")
    source = (data or {}).get("source")
    if not body or not source:
        return JSONResponse({"error": "body and source are required"},
                             status_code=400)

    def _write() -> tuple[str, bool]:
        return db.upsert_memory_by_source(
            cfg,
            scope=data.get("scope") or cfg.default_scope,
            source=source,
            title=data.get("title"),
            body=body,
            kind=data.get("kind", "reference"),
            tags=data.get("tags"),
        )

    mem_id, created = await run_in_threadpool(_write)
    return JSONResponse({"id": mem_id, "created": created, "ok": True})


async def _http_query(request: Any) -> Any:
    """REST: GET/POST /query — semantic search, or recent list when no query."""
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse

    if request.method == "POST":
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        data = dict(request.query_params)
    data = data or {}
    query = data.get("query")
    scope = data.get("scope")
    kind = data.get("kind")
    try:
        limit = max(1, min(int(data.get("limit", 10)), 50))
    except (TypeError, ValueError):
        limit = 10

    def _read() -> list:
        if query:
            return db.search(cfg, query=query, scope=scope, kind=kind, limit=limit)
        return db.recent(cfg, scope=scope, kind=kind, limit=limit)

    rows = await run_in_threadpool(_read)
    safe = json.loads(json.dumps(rows, default=str))
    return JSONResponse({"count": len(safe), "results": safe})


async def _http_readyz(request: Any) -> Any:
    """REST: GET /readyz — readiness probe reporting the active embedding mode.

    Unlike /healthz (always {"ok":true} if the process is up), this reflects
    whether semantic search is actually working. Returns 503 when embed_mode
    is 'none' so external monitoring (and a human) notices degradation that
    would otherwise be silent.
    """
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse

    from .readyz import readyz_payload

    mode = await run_in_threadpool(db.embed_mode, cfg)
    body, status = readyz_payload(mode)
    return JSONResponse(body, status_code=status)


def _build_http_app() -> Any:
    """MCP streamable-http app + REST automation routes. Appending to the app's
    own router preserves the MCP session-manager lifespan (no nested Mount)."""
    from starlette.routing import Route

    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/ingest", _http_ingest, methods=["POST"]))
    app.router.routes.append(Route("/query", _http_query, methods=["GET", "POST"]))
    app.router.routes.append(Route("/readyz", _http_readyz, methods=["GET"]))
    app.router.routes.append(Route("/catalog/upsert", _http_catalog_upsert, methods=["POST"]))
    return app


def main() -> None:
    if os.getenv("MCP_HTTP", "0").strip().lower() not in {"1", "true", "yes"}:
        mcp.run()
        return
    import uvicorn

    app = _build_http_app()
    bearer = os.getenv("MEMORY_BEARER", "").strip()
    if bearer:
        app = _BearerASGI(app, bearer)
    uvicorn.run(
        app,
        host=os.getenv("MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("MCP_PORT", "8848")),
        log_level=os.getenv("MCP_LOG", "warning"),
    )


if __name__ == "__main__":
    main()
