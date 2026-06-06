# n8n memory workflows

Automation pillar of the unified memory platform. These workflows write into
the canonical store (`zmemory-adb`) through the **memory-core gateway** REST API
(`/ingest`, `/query`) on avm-02, authenticated with a bearer token.

## Workflows

| File | Trigger | Flow |
|---|---|---|
| `01-mcp-change-log.json` | webhook `POST /webhook/mcp-change-log` | `{server,change}` → `/ingest` (kind=reference, scope=infra) |
| `02-decision-to-adr.json` | webhook `POST /webhook/decision-to-adr` | `{title,context,decision,consequences,status}` → ADR → `/ingest` (kind=decision) |
| `03-weekly-synthesis.json` | cron Sun 06:00 | `/query` recent → synthesis → `/ingest` (scope=synthesis) |
| `04-repo-graph-refresh.json` | cron daily 05:00 | `/query` → graph snapshot → `/ingest` (scope=graph) |

`00-import-probe.json` is a manual smoke test (kept for reference).

## Credential

`credentials.template.json` defines an `httpHeaderAuth` credential
`Memory Gateway Bearer` (id `MemGwBearer00001`). Replace `__MEMORY_BEARER__`
with the real token **only at import time** (keep the token out of git).

## Deploy

```bash
# token from the Mac secret store
TOK=$(grep '^MEMORY_BEARER=' ~/.secrets.d/zmemory-adb.env | cut -d= -f2)

# credential (token injected in RAM, docker cp into container, then shredded)
sed "s|__MEMORY_BEARER__|$TOK|" credentials.template.json > /dev/shm/cred.json
docker cp /dev/shm/cred.json n8n:/tmp/cred.json
docker exec n8n n8n import:credentials --input=/tmp/cred.json
docker exec -u root n8n rm -f /tmp/cred.json && shred -u /dev/shm/cred.json

# workflows (no secrets)
docker cp workflows n8n:/tmp/wf
docker exec n8n n8n import:workflow --separate --input=/tmp/wf
docker exec -u root n8n rm -rf /tmp/wf
for id in mcpchangelog001 decisiontoadr01 weeklysynth0001 repographrefr01; do
  docker exec n8n n8n update:workflow --id="$id" --active=true
done
docker compose -f /opt/n8n/docker-compose.yml restart n8n   # register webhooks/crons
```

## Network note

The n8n container (docker bridge) reaches the host gateway via a ufw rule
allowing `172.16.0.0/12 → 8848`. The gateway binds `0.0.0.0:8848`; the public
NIC is blocked by the OCI security list. Bearer auth is required on all routes
except `/healthz`.
