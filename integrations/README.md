# memory-core integrations — cross-session memory mesh

Client-side glue that wires local agents to the **memory-core** gateway so they
share one persistent, cross-session memory (Oracle ADB 23ai, vector search).
The gateway core (`src/`) and the DB schema (`db/`) are **not** touched here.

```
integrations/
├── mesh_router.py            # unified store/query client (stdlib only) + CLI
├── zmemory-mesh.env.sample   # client credential template (copy to ~/.secrets.d/)
├── claude_cli/
│   └── memory_hook.sh        # Claude CLI recall (inject) + ingest hook
├── claude_code_desktop/
│   ├── mcp_config.json       # remote MCP entry to merge into Claude Desktop
│   └── zmemory-mcp.sh        # bearer wrapper (no secret committed)
├── pi_agent/
│   ├── memory_adapter.py     # async Unix-socket daemon bridge
│   └── launchd_plist.xml     # LaunchAgent (KeepAlive auto-restart)
└── tests/                    # store->query roundtrip, degradation, CLI, adapter
```

Everything funnels through `mesh_router.py`, which speaks the gateway's REST
contract (`/ingest`, `/query`, `/healthz`) and degrades gracefully when the
gateway is unreachable (memory-less mode, never raises).

See **[../docs/MEMORY_MESH.md](../docs/MEMORY_MESH.md)** for architecture and a
per-client quick-start. No secrets live in this tree — credentials come from
`~/.secrets.d/zmemory-mesh.env` (gitignored), referenced by env-var name only.

## Run the tests

```bash
# Live tests hit the real gateway via ~/.secrets.d/zmemory-mesh.env and clean
# up after themselves; degradation/adapter tests are offline.
for t in roundtrip degradation cli pi_adapter; do
  python3 integrations/tests/test_${t}.py
done
```
