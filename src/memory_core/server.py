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
from .shaping import shape_results

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
    """Persist a memory. kind: capture|decision|fact|reference|project."""
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
    max_tokens: int | None = None,
    chunk_size: int | None = None,
    fields: list[str] | None = None,
) -> list[dict]:
    """Semantic search across memories (cosine over in-DB embeddings).

    Falls back to keyword match if the embedding model is not yet loaded.
    Optional shaping (ZMEM-Q1): max_tokens caps the total body budget
    (overflowing record is tail-truncated with truncated=true), chunk_size
    splits bodies into paragraph-aligned chunks with source#cN references,
    fields projects each record to the given keys. Omit all three for the
    unchanged legacy response.
    """
    rows = db.search(cfg, query=query, scope=scope, kind=kind,
                     limit=max(1, min(limit, 50)))
    return shape_results(rows, max_tokens=max_tokens,
                         chunk_size=chunk_size, fields=fields)


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
    """List most-recently updated memories."""
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


# ---------------------------------------------------------------------------
# ZIONOS L2 — Semantic Layer (Spec 1)
#   - https://github.com/Zekiog/zion-os/blob/main/docs/semantic-layer.md
#   - Implemented in src/memory_core/semantic.py (CATALOG + SemanticLayer
#     + KnowledgeGraph over the entities / entity_relationships /
#     entity_aliases / entity_embeddings tables).
# ---------------------------------------------------------------------------

from .semantic import CATALOG as _SEMANTIC_CATALOG  # noqa: E402
from .semantic import KnowledgeGraph, SemanticLayer  # noqa: E402

_semantic = SemanticLayer()
_semantic.register_catalog(_SEMANTIC_CATALOG)


def _kg() -> KnowledgeGraph:
    """Per-call handle so config changes propagate without restart."""
    return KnowledgeGraph(cfg)


@mcp.tool()
def search_memory(
    query: str,
    entity_type: str | None = None,
    scope: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Semantic search over the typed entity catalog.

    Returns hybrid (vector + alias) matches. Uses the existing in-DB
    `VECTOR_EMBEDDING(...)` model if available, otherwise the client-side
    fastembed fallback, otherwise keyword LIKE.

    Spec: https://github.com/Zekiog/zion-os/blob/main/docs/semantic-layer.md#mcp-tools
    """
    return _kg().search(
        scope=scope or cfg.default_scope,
        query=query,
        entity_type=entity_type,
        limit=limit,
    )


@mcp.tool()
def resolve_metric(entity: str, metric: str) -> dict | None:
    """Return the metric descriptor for an entity (catalog lookup, no DB hit)."""
    return _semantic.resolve(entity, metric)


@mcp.tool()
def add_entity(
    entity_type: str,
    attributes: dict,
    display: str | None = None,
    scope: str | None = None,
    aliases: list[str] | None = None,
    locale: str = "en",
) -> dict:
    """Persist a new typed entity + optional aliases + auto-embedding.

    Validates `entity_type` against the catalog; unknown types raise a clean
    MCP error so LLM clients can recover.
    """
    if entity_type not in _semantic:
        raise ValueError(
            f"unknown entity_type {entity_type!r}; "
            f"registered: {sorted(_semantic._entities.keys())}"
        )
    ent_id = _kg().add_entity(
        scope=scope or cfg.default_scope,
        entity_type=entity_type,
        attributes=attributes,
        display=display,
        aliases=aliases,
        locale=locale,
    )
    return {"id": ent_id, "entity_type": entity_type, "scope": scope or cfg.default_scope}


@mcp.tool()
def add_relationship(
    from_id: str,
    to_id: str,
    relation: str,
    weight: float = 1.0,
    scope: str | None = None,
) -> dict:
    """Create a typed weighted edge between two existing entities (idempotent)."""
    rid = _kg().add_relationship(
        scope=scope or cfg.default_scope,
        from_id=from_id, to_id=to_id, relation=relation, weight=weight,
    )
    return {"id": rid, "from_id": from_id, "to_id": to_id, "relation": relation}


@mcp.tool()
def query_graph(
    entity_id: str,
    relation: str | None = None,
    max_hops: int = 1,
    limit: int = 50,
    scope: str | None = None,
) -> list[dict]:
    """Graph traversal over typed entities (1..3 hops, undirected optional).

    Pass `relation` to filter edge types (e.g. "owns", "manages"). Empty
    `relation` matches any edge.
    """
    return _kg().neighbors(
        scope=scope or cfg.default_scope,
        entity_id=entity_id,
        relation=relation,
        max_hops=max_hops,
        limit=limit,
    )


class _BearerASGI:
    """Pure-ASGI bearer-token gate. SSE-safe."""

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
    """REST: POST /ingest"""
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
    """REST: POST /catalog/upsert"""
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    body = (data or {}).get("body")
    source = (data or {}).get("source")
    if not body or not source:
        return JSONResponse({"error": "body and source are required"}, status_code=400)
    def _write() -> tuple[str, bool]:
        return db.upsert_memory_by_source(
            cfg, scope=data.get("scope") or cfg.default_scope,
            source=source, title=data.get("title"), body=body,
            kind=data.get("kind", "reference"), tags=data.get("tags"),
        )
    mem_id, created = await run_in_threadpool(_write)
    return JSONResponse({"id": mem_id, "created": created, "ok": True})


async def _http_query(request: Any) -> Any:
    """REST: GET/POST /query"""
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

    def _opt_int(name: str) -> int | None:
        try:
            value = int(data[name])
        except (KeyError, TypeError, ValueError):
            return None
        return value if value > 0 else None

    max_tokens = _opt_int("max_tokens")
    chunk_size = _opt_int("chunk_size")
    fields_raw = data.get("fields")
    if isinstance(fields_raw, str):
        fields = [f.strip() for f in fields_raw.split(",") if f.strip()] or None
    elif isinstance(fields_raw, list):
        fields = [str(f) for f in fields_raw] or None
    else:
        fields = None

    def _read() -> list:
        if query:
            rows = db.search(cfg, query=query, scope=scope, kind=kind, limit=limit)
        else:
            rows = db.recent(cfg, scope=scope, kind=kind, limit=limit)
        return shape_results(rows, max_tokens=max_tokens,
                             chunk_size=chunk_size, fields=fields)

    rows = await run_in_threadpool(_read)
    safe = json.loads(json.dumps(rows, default=str))
    return JSONResponse({"count": len(safe), "results": safe})


async def _http_readyz(request: Any) -> Any:
    """REST: GET /readyz"""
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse
    from .readyz import readyz_payload
    mode = await run_in_threadpool(db.embed_mode, cfg)
    body, status = readyz_payload(mode)
    return JSONResponse(body, status_code=status)


# ---------------------------------------------------------------------------
# /v1/events
# ---------------------------------------------------------------------------

async def _http_events_post(request: Any) -> Any:
    """REST: POST /v1/events"""
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
            {"error": "source, event_type and severity are required"}, status_code=400)
    def _write() -> int:
        from .events import log_event
        return log_event(
            source=source, event_type=event_type, severity=severity,
            context=data.get("context"),
            remediation_taken=data.get("remediation_taken"),
            outcome=data.get("outcome"),
        )
    try:
        row_id = await run_in_threadpool(_write)
    except Exception as exc:
        return JSONResponse(
            {"error": "event_log write failed", "detail": str(exc)}, status_code=503)
    return JSONResponse({"id": row_id, "ok": True})


async def _http_events_get(request: Any) -> Any:
    """REST: GET /v1/events"""
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
            event_types=event_types, severity=severity,
            source=source, since_hours=since_hours, limit=limit,
        )
    try:
        rows = await run_in_threadpool(_read)
    except Exception as exc:
        return JSONResponse(
            {"error": "event_log read failed", "detail": str(exc)}, status_code=503)
    safe = json.loads(json.dumps(rows, default=str))
    return JSONResponse({"count": len(safe), "events": safe})


async def _http_events_dispatch(request: Any) -> Any:
    if request.method == "POST":
        return await _http_events_post(request)
    return await _http_events_get(request)


# ---------------------------------------------------------------------------
# HTTP app builder (with auto-migration at startup)
# ---------------------------------------------------------------------------

def _build_http_app() -> Any:
    """MCP streamable-http app + REST routes + Neon auto-migration at startup."""
    import contextlib
    from starlette.routing import Route

    app = mcp.streamable_http_app()

    # Neon auto-migration: runs migrations/*.sql at startup, fail-soft.
    @contextlib.asynccontextmanager
    async def _lifespan(app):
        try:
            from starlette.concurrency import run_in_threadpool
            from .migrate import run_migrations
            await run_in_threadpool(run_migrations)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "startup migration error (non-fatal): %s", exc
            )
        yield

    # Attach lifespan if the app supports it
    try:
        app.router.lifespan_context = _lifespan
    except AttributeError:
        pass  # older starlette: migrations must be run manually

    app.router.routes.append(Route("/ingest", _http_ingest, methods=["POST"]))
    app.router.routes.append(Route("/query", _http_query, methods=["GET", "POST"]))
    app.router.routes.append(Route("/readyz", _http_readyz, methods=["GET"]))
    app.router.routes.append(Route("/catalog/upsert", _http_catalog_upsert, methods=["POST"]))
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
