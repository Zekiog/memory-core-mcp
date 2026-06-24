#!/usr/bin/env python3
"""mesh_router — unified store/query interface to the memory-core gateway.

One small, dependency-free (stdlib urllib) client shared by every mesh client
(Claude CLI hook, Pi Agent adapter, n8n, ad-hoc scripts). It speaks the
gateway's REST contract:

    POST /ingest  {"body", "title"?, "kind"?, "scope"?, "tags"?, "source"?, "links"?}
                  -> {"id", "ok"}
    GET/POST /query {"query"?, "scope"?, "kind"?, "limit"?}
                  -> {"count", "results": [...]}
    GET  /healthz  (open, no bearer) -> {"ok": true}

Credentials are read from the environment (ZMEMORY_GATEWAY_URL,
ZMEMORY_BEARER_TOKEN) and, as a fallback, from a key=value env file
(default ~/.secrets.d/zmemory-mesh.env). No secret is ever hardcoded.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

DEFAULT_ENV_FILE = os.path.expanduser("~/.secrets.d/zmemory-mesh.env")


def _read_env_file(path: str) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file. Missing file -> empty dict."""
    out: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                out[key.strip()] = val.strip()
    except OSError:
        pass
    return out


class MeshRouter:
    """Stateless REST client for the shared memory mesh."""

    # The public edge is behind Cloudflare, whose WAF rejects default library
    # User-Agents (Python-urllib/* -> 403). Send a stable, honest product UA.
    USER_AGENT = "zmemory-mesh-router/1.0"

    def __init__(
        self,
        gateway_url: str | None = None,
        bearer: str | None = None,
        timeout: float = 10.0,
        env_file: str = DEFAULT_ENV_FILE,
    ) -> None:
        env = _read_env_file(env_file)
        self.gateway_url = (
            gateway_url
            or os.environ.get("ZMEMORY_GATEWAY_URL")
            or env.get("ZMEMORY_GATEWAY_URL")
            or ""
        ).rstrip("/")
        self.bearer = (
            bearer
            or os.environ.get("ZMEMORY_BEARER_TOKEN")
            or env.get("ZMEMORY_BEARER_TOKEN")
            or ""
        )
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.gateway_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("content-type", "application/json")
        req.add_header("user-agent", self.USER_AGENT)
        if self.bearer:
            req.add_header("authorization", f"Bearer {self.bearer}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw or "{}")

    def health(self) -> bool:
        """True if the gateway answers /healthz; never raises."""
        try:
            self._request("GET", "/healthz")
            return True
        except Exception:
            return False

    def store(
        self,
        body: str,
        *,
        title: str | None = None,
        kind: str = "fact",
        scope: str | None = None,
        source: str = "mesh-router",
        tags: dict | None = None,
        links: list[str] | None = None,
    ) -> dict:
        """Persist a memory. Returns {"ok", "id"}."""
        payload: dict = {"body": body, "kind": kind, "source": source}
        if title:
            payload["title"] = title
        if scope:
            payload["scope"] = scope
        if tags:
            payload["tags"] = tags
        if links:
            payload["links"] = links
        try:
            out = self._request("POST", "/ingest", payload)
        except Exception as exc:  # noqa: BLE001 - memory-less degradation
            return {"ok": False, "degraded": True, "error": str(exc)}
        return {"ok": bool(out.get("ok")), "id": out.get("id")}

    def query(
        self,
        text: str | None = None,
        *,
        scope: str | None = None,
        kind: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Semantic search (or recent list when text is None). Returns results."""
        payload: dict = {"limit": limit}
        if text:
            payload["query"] = text
        if scope:
            payload["scope"] = scope
        if kind:
            payload["kind"] = kind
        try:
            out = self._request("POST", "/query", payload)
        except Exception:  # noqa: BLE001 - memory-less degradation -> no inject
            return []
        return out.get("results") or []


def format_injection(results: list[dict]) -> str:
    """Render query results as a delimited context-injection block."""
    lines = [f"<<MEMORY-MESH recall: {len(results)}>>"]
    for row in results:
        scope = row.get("scope") or "?"
        kind = row.get("kind") or "?"
        title = (row.get("title") or "").strip()
        body = " ".join((row.get("body") or "").split())
        snippet = body[:200] + ("…" if len(body) > 200 else "")
        label = f" {title} —" if title else ""
        lines.append(f"- [{scope}/{kind}]{label} {snippet}  ({row.get('id')})")
    lines.append("<</MEMORY-MESH>>")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mesh_router", description="memory mesh unified store/query client"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_store = sub.add_parser("store", help="persist a memory")
    p_store.add_argument("body")
    p_store.add_argument("--title")
    p_store.add_argument("--kind", default="fact")
    p_store.add_argument("--scope")
    p_store.add_argument("--source", default="mesh-router")

    p_query = sub.add_parser("query", help="semantic search")
    p_query.add_argument("text", nargs="?")
    p_query.add_argument("--scope")
    p_query.add_argument("--kind")
    p_query.add_argument("--limit", type=int, default=10)
    p_query.add_argument(
        "--inject", action="store_true",
        help="print a context-injection block; print nothing when there are no hits",
    )

    sub.add_parser("health", help="probe gateway /healthz")

    args = parser.parse_args(argv)
    router = MeshRouter()

    if args.cmd == "health":
        ok = router.health()
        print("ok" if ok else "unreachable")
        return 0 if ok else 1

    if args.cmd == "store":
        res = router.store(
            args.body, title=args.title, kind=args.kind,
            scope=args.scope, source=args.source,
        )
        print(json.dumps(res))
        return 0 if res.get("ok") else 1

    results = router.query(
        args.text, scope=args.scope, kind=args.kind, limit=args.limit
    )
    if args.inject:
        if results:  # empty -> emit nothing, original prompt passes through as-is
            print(format_injection(results))
        return 0
    print(json.dumps({"count": len(results), "results": results}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
