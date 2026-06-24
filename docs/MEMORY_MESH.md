# Memory Mesh

A persistent, cross-session memory shared by multiple local clients through the
**memory-core** gateway. Each client stores and recalls from the same Oracle ADB
23ai vector store, so context written in one surface is available in the others.

## Architecture

```
                       ┌──────────────────────────────────────────┐
                       │  memory-core gateway   (avm-02, unchanged)│
                       │  MCP /mcp · REST /ingest /query · /healthz │
                       │  bearer-authed · Oracle ADB 23ai (vectors) │
                       └───────────────────▲──────────────────────┘
                                           │ HTTPS + Bearer
                          https://memory.finn.qzz.io   (Cloudflare edge)
                                           │
        ┌──────────────────────┬───────────┴───────────┬──────────────────────┐
        │                      │                       │                      │
 ┌──────┴───────┐      ┌───────┴────────┐      ┌───────┴────────┐     ┌───────┴───────┐
 │ Claude        │      │ Claude CLI     │      │ Pi Agent        │     │ n8n           │
 │ Desktop       │      │ (shell hook)   │      │ (socket daemon) │     │ (automation)  │
 │ mcp_config    │      │ memory_hook.sh │      │ memory_adapter  │     │ mesh_sync wf  │
 │  → /mcp       │      │  → mesh_router │      │  → mesh_router  │     │  → /ingest    │
 └──────────────┘      └───────┬────────┘      └───────┬────────┘     │     /query    │
                                │                       │              └───────────────┘
                                └─────────┬─────────────┘
                                          ▼
                                  mesh_router.py
                          unified store/query · graceful degrade
```

- **Desktop** talks MCP/HTTP natively (via `mcp-remote`).
- **CLI** and **Pi Agent** share `mesh_router.py` (REST, stdlib-only).
- **n8n** calls the same REST endpoints for cross-client event routing.

## Gateway contract (verified)

| Endpoint | Method | Request | Response |
|---|---|---|---|
| `/healthz` | GET | — (open, no bearer) | `{"ok": true}` |
| `/ingest` | POST | `{"body"*, "title", "kind", "scope", "tags", "source", "links"}` | `{"id", "ok": true}` |
| `/query` | GET/POST | `{"query", "scope", "kind", "limit"}` | `{"count", "results": [...]}` |

Auth: `Authorization: Bearer <token>`. The public edge sits behind Cloudflare,
which rejects default library User-Agents — `mesh_router.py` sends a product UA.

## Credentials (zero secrets in git)

```bash
cp integrations/zmemory-mesh.env.sample ~/.secrets.d/zmemory-mesh.env
chmod 600 ~/.secrets.d/zmemory-mesh.env
# set ZMEMORY_BEARER_TOKEN to the gateway's MEMORY_BEARER
```

Every client reads `ZMEMORY_GATEWAY_URL` and `ZMEMORY_BEARER_TOKEN` from the
environment, falling back to that file. Nothing in this repo contains a token.

## Quick-start per client

### Claude CLI (shell hook)

```jsonc
// ~/.claude/settings.json
"hooks": {
  "UserPromptSubmit": [{ "command": "/ABS/PATH/integrations/claude_cli/memory_hook.sh recall" }],
  "Stop":            [{ "command": "/ABS/PATH/integrations/claude_cli/memory_hook.sh ingest" }]
}
```
`recall` injects a `<<MEMORY-MESH>>` block when there are hits and **nothing**
when empty (the prompt passes through unchanged).

### Claude Desktop (MCP/HTTP)

Merge `integrations/claude_code_desktop/mcp_config.json` into
`~/Library/Application Support/Claude/claude_desktop_config.json`, then restart
Desktop. The `zmemory` server runs `zmemory-mcp.sh`, which sources the secret
file and execs `mcp-remote https://memory.finn.qzz.io/mcp` with the bearer.
Settings → Connectors/MCP should show **zmemory** connected.

### Pi Agent (Unix-socket daemon)

```bash
cp integrations/pi_agent/launchd_plist.xml ~/Library/LaunchAgents/io.zmemory.mesh-adapter.plist
launchctl load -w ~/Library/LaunchAgents/io.zmemory.mesh-adapter.plist   # KeepAlive auto-restart
```
Then any local process speaks one JSON line over `~/.pi/run/zmemory-mesh.sock`:

```bash
echo '{"op":"query","text":"mesh topology","limit":3}' | nc -U ~/.pi/run/zmemory-mesh.sock
echo '{"op":"store","body":"note","scope":"pi-agent"}' | nc -U ~/.pi/run/zmemory-mesh.sock
```

### n8n (automation)

Import `n8n/workflows/mesh_sync_workflow.json`. Provide `ZMEMORY_GATEWAY_URL`
and `ZMEMORY_BEARER_TOKEN` to the n8n environment (or swap the header for an
HTTP Header Auth credential). `POST /webhook/mesh-sync` ingests an event; a
15-minute schedule pulses `/query` for cross-client routing.

## Edge-case behavior

| Situation | Behavior |
|---|---|
| Gateway unreachable | `store()` → `{"ok": false, "degraded": true}`, `query()` → `[]`. No exception (memory-less mode). |
| Empty query results | No injection emitted; original prompt unchanged. |
| Pi adapter backend slower than `ZMEMORY_IPC_TIMEOUT` (15s) | Returns `{"ok": false, "error": "timeout"}`; daemon keeps serving. |
| Bad/garbage socket request | Error dict; daemon never crashes. |

## Tests

```bash
python3 integrations/tests/test_roundtrip.py     # live store -> query (self-cleaning)
python3 integrations/tests/test_degradation.py    # offline graceful-degradation
python3 integrations/tests/test_cli.py            # CLI inject / skip-on-empty
python3 integrations/tests/test_pi_adapter.py     # adapter ops + IPC timeout
```

Live tests use `~/.secrets.d/zmemory-mesh.env`, write to scope `mesh-selftest`,
and delete their sentinel via `~/.claude/bin/zmem` afterward.
