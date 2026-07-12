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


# ---------------------------------------------------------------------------
# /v1/events — Event-sourced memory bus endpoint
# ---------------------------------------------------------------------------
# POST /v1/events  : log_event() -> Neon event_log INSERT
# GET  /v1/events  : query_events() with optional filters
#
# FAIL-SOFT INVARIANT: This endpoint is the event bus surface.
# A failure here MUST return 503 and NEVER propagate to the caller's
# memory operation. Callers (Agent-Z, OpenClaw, n8n) are expected to
# fire-and-forget with a try/except so memory calls are never blocked.
#
# Schema contract: see docs/event-sourced-memory-bus/event-schema.md
# Neon DDL:        see docs/event-sourced-memory-bus/projections.sql
# ---------------------------------------------------------------------------

async def _http_events_post(request: Any) -> Any:
    """REST: POST /v1/events

    Body (JSON):
      source          str   required   e.g. "agent-z:compose", "openclaw:sync"
      event_type      str   required   see EventType enum in events.py
      severity        str   required   info | warn | error | critical
      context         obj   optional   agent_id, workspace, surface, ...
      remediation_taken str optional
      outcome         str   optional

    Returns: {"id": <int>, "ok": true}

    MEM_* events SHOULD include in context:
      agent_id, workspace, surface, operation_id, payload_kind
    """
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    source = (data or {}).get("source")
    event_type = (data or {}).get("event_type")
    severity = (data or {}).get("severity")

    if not source or not event_type or not severity:
        return JSONResponse(
            {"error": "source, event_type and severity are required"},
            status_code=400,
        )

    def _write() -> int:
        from .events import log_event
        return log_event(
            source=source,
            event_type=event_type,
            severity=severity,
            context=data.get("context"),
            remediation_taken=data.get("remediation_taken"),
            outcome=data.get("outcome"),
        )

    try:
        row_id = await run_in_threadpool(_write)
    except Exception as exc:
        # FAIL-SOFT: never crash callers; return 503 so monitoring sees it
        return JSONResponse(
            {"error": "event_log write failed", "detail": str(exc)},
            status_code=503,
        )

    return JSONResponse({"id": row_id, "ok": True})


async def _http_events_get(request: Any) -> Any:
    """REST: GET /v1/events?event_type=mem_ingest&severity=error&source=agent-z&since_hours=24&limit=50"""
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse

    params = dict(request.query_params)
    event_types_raw = params.get("event_type")
    event_types = [t.strip() for t in event_types_raw.split(",")] if event_types_raw else None
    severity = params.get("severity")
    source = params.get("source")
    try:
        since_hours = int(params.get("since_hours", 24))
        limit = max(1, min(int(params.get("limit", 100)), 500))
    except (TypeError, ValueError):
        since_hours, limit = 24, 100

    def _read() -> list:
        from .events import query_events
        return query_events(
            event_types=event_types,
            severity=severity,
            source=source,
            since_hours=since_hours,
            limit=limit,
        )

    try:
        rows = await run_in_threadpool(_read)
    except Exception as exc:
        return JSONResponse(
            {"error": "event_log read failed", "detail": str(exc)},
            status_code=503,
        )

    safe = json.loads(json.dumps(rows, default=str))
    return JSONResponse({"count": len(safe), "events": safe})


async def _http_events_dispatch(request: Any) -> Any:
    """Route /v1/events to GET or POST handler."""
    if request.method == "POST":
        return await _http_events_post(request)
    return await _http_events_get(request)


def _build_http_app() -> Any:
    """MCP streamable-http app + REST automation routes. Appending to the app's
    own router preserves the MCP session-manager lifespan (no nested Mount)."""
    from starlette.routing import Route

    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/ingest", _http_ingest, methods=["POST"]))
    app.router.routes.append(Route("/query", _http_query, methods=["GET", "POST"]))
    app.router.routes.append(Route("/readyz", _http_readyz, methods=["GET"]))
    app.router.routes.append(Route("/catalog/upsert", _http_catalog_upsert, methods=["POST"]))
    # Event-sourced memory bus
    app.router.routes.append(Route("/v1/events", _http_events_dispatch, methods=["GET", "POST"]))
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
