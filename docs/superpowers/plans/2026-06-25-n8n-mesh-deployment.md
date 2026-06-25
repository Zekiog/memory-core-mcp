# n8n Memory Mesh Sync — Deployment / Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan **inline** in the current session. **Do not** dispatch via `subagent-driven-development` — steps touch live prod (`avm-02`), the production `.env`, and secrets over ssh; delegation to a fresh subagent is unsafe. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Memory Mesh Sync` heartbeat arm live on avm-02 — one workflow-JSON auth correction, env wiring, verify-first dry-run, `active:true`, and a single e2e Slack transition.

**Architecture:** PR #2 already merged the heartbeat logic (Code + IF + Slack) to `main` with 30/30 unit tests green; the workflow has never been imported to avm-02. This plan: (1) on branch `spec/n8n-mesh-deployment`, replace the manual `Authorization: Bearer {{ $env.ZMEMORY_BEARER_TOKEN }}` header on both HTTP nodes with the existing `httpHeaderAuth` credential `Memory Gateway Bearer` (id `MemGwBearer00001`); (2) wire `ZMEMORY_GATEWAY_URL` + `SLACK_MESH_ALERT_WEBHOOK` into `/opt/n8n/.env` over ssh (never echoed); (3) import the workflow inactive into the n8n container; (4) run four verify-first checks (auth/shape, onError item shape, classify + staticData persistence across 2 runs, Slack delivery); (5) flip `active:true`; (6) force one real transition to confirm the alert→recover loop.

**Tech Stack:** n8n v2.22.4 (queue mode: main + worker + redis + postgres-16), Docker Compose at `/opt/n8n`, n8n-nodes-base.{httpRequest@4.2, scheduleTrigger@1.2, code@2, if@2, webhook@2, respondToWebhook@1}, Node 20 (host) for `node --test` pre-flight, ssh alias `z-agentic-vm-02` (public-IP bastion), Slack incoming webhook.

**Security ground rules (apply to every task):**
- Never `echo`, `cat`, or `printf` the Slack webhook URL or any bearer/secret. Only **key-name + presence** checks against `.env`.
- Never read `/opt/n8n/.env` values (file is `chmod 600 ubuntu:ubuntu`; ssh user lacks read unless sudo — keep it that way).
- Never `--no-verify`, `--no-gpg-sign`, force-push, or amend; **no `Co-Authored-By` trailer** (attribution disabled globally).
- Stop cleanly at any user-gated blocker; resume only on explicit "continue".

**zmem coordinator checkpoints:** STARTED (already written: `55156A3894FC5224E063545E000AA403`) → DECISION_LOCKED (Task 2 end) → MUTATION_DONE (Task 7 end) → VERIFIED (Task 11 end) → HANDOFF_COMPLETE (Task 14 end).

---

## File Structure

| File | Purpose | Action |
|---|---|---|
| `n8n/workflows/mesh_sync_workflow.json` | Workflow JSON (single source of truth) | Modify both HTTP nodes |
| `/opt/n8n/.env` *(on avm-02)* | Container env (chmod 600) | Append 2 lines via ssh |
| n8n SQLite/Postgres workflow row | Live workflow | Created by `n8n import:workflow` |
| `~/.claude/bin/zmem` | Coordinator log to ADB | 3 write-backs (DECISION_LOCKED, VERIFIED, HANDOFF_COMPLETE) |

No new repo files. No edits to `heartbeat.mjs`, no edits to `tests/`, no edits to `docker-compose.yml`.

---

## Task 1: Pre-flight — confirm branch, working tree, and unit-test parity

**Files:**
- Read: `/Users/z/src/memory-core-mcp/n8n/lib/heartbeat.mjs`
- Read: `/Users/z/src/memory-core-mcp/n8n/workflows/mesh_sync_workflow.json`

- [ ] **Step 1.1: Confirm branch and clean working tree**

Run:
```bash
cd /Users/z/src/memory-core-mcp && git status && git rev-parse --abbrev-ref HEAD
```
Expected: `On branch spec/n8n-mesh-deployment` and `nothing to commit, working tree clean`.

- [ ] **Step 1.2: Run unit tests (cheap pre-flight; same logic is inlined in Code node)**

Run:
```bash
cd /Users/z/src/memory-core-mcp && node --test n8n/lib/
```
Expected: `# pass 30` `# fail 0`. If anything fails: **STOP** — do not touch the workflow JSON; escalate.

- [ ] **Step 1.3: Verify reachability to avm-02 over ssh**

Run:
```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 z-agentic-vm-02 'echo ok && uname -n'
```
Expected: `ok` followed by the host's name. If the ssh prompts for password or returns non-zero: **STOP** — ssh agent or alias is broken.

- [ ] **Step 1.4: Verify in-mesh gateway endpoints from avm-02**

Run:
```bash
ssh z-agentic-vm-02 'curl -sS -o /dev/null -w "healthz=%{http_code}\n" http://10.10.0.2:8848/healthz && curl -sS -o /dev/null -w "query_unauth=%{http_code}\n" -X POST http://10.10.0.2:8848/query -H "Content-Type: application/json" -d "{\"limit\":1}"'
```
Expected: `healthz=200` and `query_unauth=401`. The 401 is the live proof that the auth correction in Task 2 is mandatory.

---

## Task 2: Edit `mesh_sync_workflow.json` — auth correction on both HTTP nodes

**Files:**
- Modify: `n8n/workflows/mesh_sync_workflow.json` (lines 18–39 `Store -> Mesh`, lines 62–83 `Recent Pulse`)

**Credential reference (safe to commit — id is an opaque pointer, not a secret):**
- id: `MemGwBearer00001`
- name: `Memory Gateway Bearer`

- [ ] **Step 2.1: Replace the `Store -> Mesh` node block (auth correction)**

Use Edit on `n8n/workflows/mesh_sync_workflow.json`.

`old_string`:
```
    {
      "parameters": {
        "method": "POST",
        "url": "={{ $env.ZMEMORY_GATEWAY_URL }}/ingest",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "Authorization", "value": "=Bearer {{ $env.ZMEMORY_BEARER_TOKEN }}" },
            { "name": "Content-Type", "value": "application/json" }
          ]
        },
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ body: $json.body, title: $json.title, kind: $json.kind || 'fact', scope: $json.scope, source: $json.source || 'n8n-mesh-sync', tags: $json.tags }) }}",
        "options": {}
      },
      "id": "http-ingest",
      "name": "Store -> Mesh",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [480, 300]
    },
```

`new_string`:
```
    {
      "parameters": {
        "method": "POST",
        "url": "={{ $env.ZMEMORY_GATEWAY_URL }}/ingest",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "Content-Type", "value": "application/json" }
          ]
        },
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ body: $json.body, title: $json.title, kind: $json.kind || 'fact', scope: $json.scope, source: $json.source || 'n8n-mesh-sync', tags: $json.tags }) }}",
        "options": {}
      },
      "id": "http-ingest",
      "name": "Store -> Mesh",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [480, 300],
      "credentials": {
        "httpHeaderAuth": {
          "id": "MemGwBearer00001",
          "name": "Memory Gateway Bearer"
        }
      }
    },
```

- [ ] **Step 2.2: Replace the `Recent Pulse` node block (auth correction; keep User-Agent and onError)**

`old_string`:
```
    {
      "parameters": {
        "method": "POST",
        "url": "={{ $env.ZMEMORY_GATEWAY_URL }}/query",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "Authorization", "value": "=Bearer {{ $env.ZMEMORY_BEARER_TOKEN }}" },
            { "name": "User-Agent", "value": "zmemory-mesh-heartbeat/1.0" }
          ]
        },
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ limit: 1 }) }}",
        "options": {}
      },
      "id": "http-poll",
      "name": "Recent Pulse",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [480, 540],
      "onError": "continueRegularOutput"
    },
```

`new_string`:
```
    {
      "parameters": {
        "method": "POST",
        "url": "={{ $env.ZMEMORY_GATEWAY_URL }}/query",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "User-Agent", "value": "zmemory-mesh-heartbeat/1.0" }
          ]
        },
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ limit: 1 }) }}",
        "options": {}
      },
      "id": "http-poll",
      "name": "Recent Pulse",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [480, 540],
      "onError": "continueRegularOutput",
      "credentials": {
        "httpHeaderAuth": {
          "id": "MemGwBearer00001",
          "name": "Memory Gateway Bearer"
        }
      }
    },
```

- [ ] **Step 2.3: Validate the edited JSON parses**

Run:
```bash
cd /Users/z/src/memory-core-mcp && node -e 'JSON.parse(require("fs").readFileSync("n8n/workflows/mesh_sync_workflow.json","utf8")); console.log("json_ok")'
```
Expected: `json_ok`.

- [ ] **Step 2.4: Confirm `ZMEMORY_BEARER_TOKEN` is no longer referenced anywhere**

Run:
```bash
cd /Users/z/src/memory-core-mcp && grep -rn 'ZMEMORY_BEARER_TOKEN' n8n/ || echo "no_refs"
```
Expected: `no_refs`. If any match remains: revisit Step 2.1 / 2.2 — the auth header was not fully removed.

- [ ] **Step 2.5: Confirm credential reference is wired exactly twice**

Run:
```bash
cd /Users/z/src/memory-core-mcp && grep -c 'MemGwBearer00001' n8n/workflows/mesh_sync_workflow.json
```
Expected: `2`.

- [ ] **Step 2.6: Show the diff for human eyeball-review before commit**

Run:
```bash
cd /Users/z/src/memory-core-mcp && git diff -- n8n/workflows/mesh_sync_workflow.json
```
Expected: only the two HTTP node blocks change; no other node changed; `"active": false` still false; `heartbeat.mjs`-derived Code node untouched.

- [ ] **Step 2.7: Commit the auth correction**

Run:
```bash
cd /Users/z/src/memory-core-mcp && git add n8n/workflows/mesh_sync_workflow.json && git commit -m "fix(mesh-sync): use httpHeaderAuth credential instead of env bearer

Switch Store -> Mesh and Recent Pulse to authentication:
genericCredentialType / genericAuthType: httpHeaderAuth with the
existing live credential 'Memory Gateway Bearer' (id MemGwBearer00001).
Drop the manual Authorization: Bearer {{ \$env.ZMEMORY_BEARER_TOKEN }}
header on both nodes and stop referencing ZMEMORY_BEARER_TOKEN, which
was never wired into /opt/n8n/.env on avm-02 and would have caused
every /query poll to 401."
```
Expected: one commit on `spec/n8n-mesh-deployment`. The credential id is a non-secret opaque pointer — safe to land in git.

- [ ] **Step 2.8: zmem write-back — DECISION_LOCKED**

Run:
```bash
~/.claude/bin/zmem add \
  --title "n8n mesh deployment — DECISION_LOCKED" \
  --kind capture \
  --scope cli-claude-code \
  --source claude-code \
  --tags '{"phase":"deployment","status":"DECISION_LOCKED","claim":"55156A3894FC5224E063545E000AA403","branch":"spec/n8n-mesh-deployment"}' \
  --link 'https://github.com/Zekiog/memory-core-mcp/tree/spec/n8n-mesh-deployment' \
  "Auth correction committed to mesh_sync_workflow.json. Both HTTP nodes now use httpHeaderAuth credential MemGwBearer00001 ('Memory Gateway Bearer'). Manual Authorization header and ZMEMORY_BEARER_TOKEN reference removed. ZMEMORY_BEARER_TOKEN will NOT be added to .env. Next: wire ZMEMORY_GATEWAY_URL + SLACK_MESH_ALERT_WEBHOOK into /opt/n8n/.env."
```
Expected: JSON with a new id; record it for the chain (becomes parent of subsequent write-backs).

---

## Task 3: Wire `/opt/n8n/.env` on avm-02 (secret-safe, via ssh)

**Files:**
- Modify: `/opt/n8n/.env` *(remote on avm-02; chmod 600 ubuntu:ubuntu)*

**Two appended lines (key-only shown here; the webhook URL is supplied at runtime and never echoed):**

```
ZMEMORY_GATEWAY_URL=http://10.10.0.2:8848
SLACK_MESH_ALERT_WEBHOOK=<provided-at-runtime>
```

- [ ] **Step 3.1: Confirm both keys are currently absent (pre-image)**

Run:
```bash
ssh z-agentic-vm-02 'sudo -n grep -c "^ZMEMORY_GATEWAY_URL=" /opt/n8n/.env; sudo -n grep -c "^SLACK_MESH_ALERT_WEBHOOK=" /opt/n8n/.env' 2>&1 | tail -n 2
```
Expected: `0` then `0`. If non-zero, **STOP** — keys already present; redirect to "update in place" path (out of scope for this plan).

- [ ] **Step 3.2: Snapshot `.env` before mutation (recovery point)**

Run:
```bash
ssh z-agentic-vm-02 'sudo -n cp -a /opt/n8n/.env /opt/n8n/.env.bak.2026-06-25 && sudo -n ls -l /opt/n8n/.env /opt/n8n/.env.bak.2026-06-25'
```
Expected: backup exists with identical size + perms (`-rw------- ubuntu ubuntu`).

- [ ] **Step 3.3: Append `ZMEMORY_GATEWAY_URL` (non-secret, safe to echo)**

Run (single line, `tee -a` with sudo via heredoc-free redirect — value is non-secret):
```bash
ssh z-agentic-vm-02 'echo "ZMEMORY_GATEWAY_URL=http://10.10.0.2:8848" | sudo -n tee -a /opt/n8n/.env >/dev/null && sudo -n grep -c "^ZMEMORY_GATEWAY_URL=" /opt/n8n/.env'
```
Expected: `1`.

- [ ] **Step 3.4: Append `SLACK_MESH_ALERT_WEBHOOK` (SECRET — never echoed)**

**Hand-off pattern** (the human operator runs this from their terminal so the URL never enters the model's stdout/tool log):

```bash
# Operator runs locally; replace <PASTE_URL> in place; do NOT echo afterward.
read -r -s SMW
ssh z-agentic-vm-02 "printf 'SLACK_MESH_ALERT_WEBHOOK=%s\n' '$SMW' | sudo -n tee -a /opt/n8n/.env >/dev/null"
unset SMW
ssh z-agentic-vm-02 'sudo -n grep -c "^SLACK_MESH_ALERT_WEBHOOK=" /opt/n8n/.env'
```
Expected (last command): `1`. The webhook URL must not appear in any agent transcript, only in the operator's shell.

- [ ] **Step 3.5: Verify line count grew by exactly 2 and perms unchanged**

Run:
```bash
ssh z-agentic-vm-02 'echo bak=$(sudo -n wc -l < /opt/n8n/.env.bak.2026-06-25) cur=$(sudo -n wc -l < /opt/n8n/.env); sudo -n stat -c "%a %U:%G" /opt/n8n/.env'
```
Expected: `cur` = `bak` + 2; permissions `600 ubuntu:ubuntu`.

- [ ] **Step 3.6: Restart the n8n stack to pick up env**

Run:
```bash
ssh z-agentic-vm-02 'cd /opt/n8n && sudo -n docker compose restart n8n n8n-worker && sleep 8 && sudo -n docker compose ps'
```
Expected: both `n8n` and `n8n-worker` show `running (healthy)` or `Up`. Postgres + redis stay running (not restarted).

- [ ] **Step 3.7: Confirm env reached the container (key presence only)**

Run:
```bash
ssh z-agentic-vm-02 'sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T n8n sh -c "printenv | grep -E ^\(ZMEMORY_GATEWAY_URL\|SLACK_MESH_ALERT_WEBHOOK\)= | sed -E s/=.*/=PRESENT/"'
```
Expected:
```
ZMEMORY_GATEWAY_URL=PRESENT
SLACK_MESH_ALERT_WEBHOOK=PRESENT
```
The `sed` strips the value before it ever reaches the terminal — never remove that filter.

---

## Task 4: Import the workflow (inactive) into the n8n container

**Files:**
- Source: `/Users/z/src/memory-core-mcp/n8n/workflows/mesh_sync_workflow.json` (committed on `spec/n8n-mesh-deployment`)
- Target: avm-02 → `/tmp/mesh_sync_workflow.json` inside `n8n` container → workflow row in Postgres

- [ ] **Step 4.1: Copy the JSON to avm-02**

Run:
```bash
scp /Users/z/src/memory-core-mcp/n8n/workflows/mesh_sync_workflow.json z-agentic-vm-02:/tmp/mesh_sync_workflow.json
```
Expected: 1 file copied, no permission warnings.

- [ ] **Step 4.2: Copy the file into the n8n container**

Run:
```bash
ssh z-agentic-vm-02 'sudo -n docker cp /tmp/mesh_sync_workflow.json $(sudo -n docker compose -f /opt/n8n/docker-compose.yml ps -q n8n):/tmp/mesh_sync_workflow.json && sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T n8n ls -l /tmp/mesh_sync_workflow.json'
```
Expected: file listed inside container, owner `node` (uid 1000), size > 0.

- [ ] **Step 4.3: Pre-import collision check**

Run:
```bash
ssh z-agentic-vm-02 'sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T n8n n8n list:workflow' | awk -F'|' '/Memory Mesh Sync/ {print "COLLISION:" $0}'
```
Expected: no `COLLISION:` line. (A `workflow-mem-memory-core-sync` row may exist — that's distinct, not a collision; leave it alone per spec §7.) If `Memory Mesh Sync` already exists: **STOP** — that contradicts pre-window recon; escalate.

- [ ] **Step 4.4: Import the workflow (inactive — JSON has `"active": false`)**

Run:
```bash
ssh z-agentic-vm-02 'sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T n8n n8n import:workflow --input=/tmp/mesh_sync_workflow.json'
```
Expected: `Successfully imported 1 workflow.` (exact wording from n8n v2.22.4).

- [ ] **Step 4.5: Capture the live workflow id**

Run:
```bash
ssh z-agentic-vm-02 'sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T n8n n8n list:workflow' | awk -F'|' '/Memory Mesh Sync/ {gsub(/ /,"",$1); print "WF_ID="$1}'
```
Expected: a single `WF_ID=<id>` line. Record `<id>` — used by Tasks 9 and 11. Set it locally:
```bash
WF_ID=<id-from-above>
```

- [ ] **Step 4.6: Confirm credential is attached on both nodes**

Run:
```bash
ssh z-agentic-vm-02 "sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T postgres psql -U n8n -d n8n -tAc \"select jsonb_agg(node->'credentials') from (select jsonb_array_elements(nodes) as node from workflow_entity where name='Memory Mesh Sync') t where node->>'name' in ('Store -> Mesh','Recent Pulse');\""
```
Expected: two `httpHeaderAuth` cred objects with `\"id\": \"MemGwBearer00001\"`. If null on either: import dropped the credential reference — fall back to UI attach (spec §7 fallback) via `ssh -L 5678:10.10.0.2:5678 z-agentic-vm-02` → http://localhost:5678 → workflow editor → attach `Memory Gateway Bearer` to both HTTP nodes → save.

---

## Task 5: Verify #1 — auth + response shape (live `/query`)

**Goal:** Prove `Memory Gateway Bearer` credential injects a valid token. 401 here means the credential's stored secret is wrong, not the workflow.

- [ ] **Step 5.1: Open the SSH tunnel for UI access**

Run (in a separate terminal; keep open through Task 11):
```bash
ssh -L 5678:10.10.0.2:5678 -N z-agentic-vm-02
```
Expected: no output; tunnel established. Verify in another shell:
```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:5678/healthz
```
Expected: `200`.

- [ ] **Step 5.2: Open the workflow in the editor**

In a browser: http://localhost:5678 → Workflows → `Memory Mesh Sync` → confirm `Inactive` toggle, both HTTP nodes show `Memory Gateway Bearer` under Credentials.

- [ ] **Step 5.3: Execute `Recent Pulse` once (single-node run)**

In the editor, click `Recent Pulse` → **Execute step**. Observe the node output.

Expected:
- HTTP status: `200`
- Output JSON shape: `{ "count": <int ≥ 0>, "results": [ ... ] }`
- No `error` property on `item.json`.

If `401`: credential's stored bearer is wrong → **STOP**, escalate (out of scope: rotate via Settings → Credentials).

---

## Task 6: Verify #2 — `onError` item shape (the last live risk)

**Goal:** Confirm that when `Recent Pulse` fails network/HTTP, the downstream item carries `item.json.error` — the exact property `Evaluate Heartbeat` keys on (`httpFailed = !!(item.json && item.json.error)`).

- [ ] **Step 6.1: Temporarily point `Recent Pulse` URL at an unreachable port**

In the editor: `Recent Pulse` → URL field → change to:
```
http://10.10.0.2:9/query
```
Save the workflow (still inactive).

- [ ] **Step 6.2: Execute the schedule path once**

Click the `Every 15m` trigger → **Execute workflow**. Open the most recent execution log.

Expected:
- `Recent Pulse` shows a failed call (red / warning), but the workflow **continues** (because `onError: continueRegularOutput`).
- `Evaluate Heartbeat` runs and emits `{ "alert": true, "severity": "down", "current": "down", "text": "🔴 *Memory Mesh DOWN* ..." }`.

Inspect the data passed from `Recent Pulse` to `Evaluate Heartbeat` — confirm the item JSON has an `error` field (any of `{ error: "..." }` or `{ error: { ... } }` qualifies; the Code node uses `!!(item.json && item.json.error)`).

If the item has no `error` field: **STOP** — the Code node's `httpFailed` check needs to be revised. Do not proceed. (This is the predecessor spec's stated unknown; failing here means design rev, not a hot-fix.)

- [ ] **Step 6.3: Restore the URL**

Editor: `Recent Pulse` → URL → revert to:
```
={{ $env.ZMEMORY_GATEWAY_URL }}/query
```
Save.

- [ ] **Step 6.4: Sanity re-run to confirm restoration**

Click `Recent Pulse` → **Execute step** → expect `200` and `{ count, results }` again. If still 503/timeout, the URL field didn't save; redo Step 6.3.

---

## Task 7: Verify #3 — classify + `staticData` persistence across two runs

**Goal:** Prove (a) on a healthy gateway the classifier emits `current:'ok'`, `alert:false`; (b) `$getWorkflowStaticData('global').meshHeartbeat` is persisted between executions in queue mode (schedule on main, execute on worker, write-back to Postgres workflow row).

- [ ] **Step 7.1: First scheduled-path execution**

Editor: click `Every 15m` → **Execute workflow**. Inspect `Evaluate Heartbeat` output.

Expected: `{ "alert": false, "severity": null, "current": "ok", "ageMin": <int>, "text": null }`.

- [ ] **Step 7.2: Inspect persisted state**

Run:
```bash
ssh z-agentic-vm-02 "sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T postgres psql -U n8n -d n8n -tAc \"select jsonb_extract_path(static_data::jsonb,'global','meshHeartbeat') from workflow_entity where name='Memory Mesh Sync';\""
```
Expected: a JSON object like `{"state":"ok","since":"<iso>","lastNewestTs":"<iso>"}`. If `null` or missing: queue-mode static-data write-back is broken; **STOP** and escalate (not a hot-fix).

- [ ] **Step 7.3: Second execution (≥30 seconds later)**

Wait 30s. Editor: click `Every 15m` → **Execute workflow** again. Inspect `Evaluate Heartbeat` output again.

Expected: still `alert:false`, `current:'ok'`. The classifier did **not** alert because the prior state was `ok` and the current state is `ok` (no transition).

- [ ] **Step 7.4: Confirm `since` did not change (proves prevSince was read)**

Run:
```bash
ssh z-agentic-vm-02 "sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T postgres psql -U n8n -d n8n -tAc \"select jsonb_extract_path_text(static_data::jsonb,'global','meshHeartbeat','since') from workflow_entity where name='Memory Mesh Sync';\""
```
Expected: same ISO timestamp as Step 7.2 (proves the Code node read prior state and carried `nextSince := prevSince` because state didn't change).

---

## Task 8: Verify #4 — Slack delivery (forced send)

**Goal:** Prove the `Notify Slack` node can reach the configured webhook and the message renders. The check is **delivery**, not transition logic (that's covered in Task 11).

- [ ] **Step 8.1: Manually execute `Notify Slack` with a test payload**

Editor: click `Notify Slack` → click the input panel → click **Edit Input Data** → paste:
```json
[{"text":"✅ verify-step: ignore — n8n mesh deployment test from avm-02"}]
```
Click **Execute step**.

Expected:
- Node returns HTTP `200` with body `ok` (Slack incoming-webhook contract).
- A message reading `✅ verify-step: ignore — n8n mesh deployment test from avm-02` appears in the target channel.

If `400 invalid_payload`: the webhook URL is correct but the channel was deleted/archived; recreate.
If `403`/`404`: the webhook URL was revoked/rotated — re-run Task 3 Step 3.4 with a fresh URL.

- [ ] **Step 8.2: Post the "ignore me" cleanup note in Slack manually**

Send a follow-up message in the channel like `(verify cleanup — ignore the previous test)` so the channel reads cleanly post-deploy. No tool action required.

---

## Task 9: Activate the workflow

- [ ] **Step 9.1: Flip `active:true` via CLI**

Run (`<WF_ID>` from Task 4.5):
```bash
ssh z-agentic-vm-02 "sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T n8n n8n update:workflow --id=<WF_ID> --active=true"
```
Expected: `Successfully updated 1 workflow.` (or equivalent v2.22.4 success line).

- [ ] **Step 9.2: Confirm activation in the editor and DB**

UI: http://localhost:5678 → `Memory Mesh Sync` → confirm `Active` toggle = on.

DB cross-check:
```bash
ssh z-agentic-vm-02 "sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T postgres psql -U n8n -d n8n -tAc \"select id,active from workflow_entity where name='Memory Mesh Sync';\""
```
Expected: `<WF_ID>|t`.

- [ ] **Step 9.3: Confirm the schedule is registered on main**

Run:
```bash
ssh z-agentic-vm-02 'sudo -n docker compose -f /opt/n8n/docker-compose.yml logs --since 60s n8n' | grep -iE 'schedule|trigger' | tail -n 5
```
Expected: one line referencing the workflow id or name and the 15-minute interval being scheduled. Absence is not failure (logs may be quiet) — proceed.

---

## Task 10: e2e — forced real transition (down → recovered)

**Goal:** Drive one real `ok → down → ok` transition through the live system and confirm exactly two Slack messages land in order.

- [ ] **Step 10.1: Pre-record current `staticData` for the audit trail**

Run:
```bash
ssh z-agentic-vm-02 "sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T postgres psql -U n8n -d n8n -tAc \"select jsonb_extract_path(static_data::jsonb,'global','meshHeartbeat') from workflow_entity where name='Memory Mesh Sync';\""
```
Note the result. Expected: `{"state":"ok",...}`.

- [ ] **Step 10.2: Force a `down` by stopping the gateway**

`memory-core` runs at `10.10.0.2:8848`. Stop it briefly:
```bash
ssh z-agentic-vm-02 'sudo -n systemctl stop memory-core || sudo -n docker stop memory-core'
```
(Whichever command applies; only one will succeed — the other returns non-zero and is harmless.)

- [ ] **Step 10.3: Trigger one scheduled-path execution and confirm Slack `DOWN`**

Editor: click `Every 15m` → **Execute workflow** → confirm `Evaluate Heartbeat` output is `{ alert:true, severity:'down', ... }` and a **🔴 Memory Mesh DOWN** message lands in the target channel.

- [ ] **Step 10.4: Restart the gateway**

Run:
```bash
ssh z-agentic-vm-02 'sudo -n systemctl start memory-core || sudo -n docker start memory-core'
```
Wait ~5s. Verify health:
```bash
ssh z-agentic-vm-02 'curl -sS -o /dev/null -w "%{http_code}\n" http://10.10.0.2:8848/healthz'
```
Expected: `200`.

- [ ] **Step 10.5: Trigger one more execution and confirm Slack `RECOVERED`**

Editor: click `Every 15m` → **Execute workflow** → confirm `Evaluate Heartbeat` output is `{ alert:true, severity:'recovered', ... }` and a **✅ Memory Mesh RECOVERED** message lands in the channel.

- [ ] **Step 10.6: Confirm steady-state returns to `ok` with no further alerts**

Editor: click `Every 15m` → **Execute workflow** one more time → expect `{ alert:false, current:'ok' }` and **no** Slack message.

- [ ] **Step 10.7: Post-state DB check**

Run:
```bash
ssh z-agentic-vm-02 "sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T postgres psql -U n8n -d n8n -tAc \"select jsonb_extract_path(static_data::jsonb,'global','meshHeartbeat') from workflow_entity where name='Memory Mesh Sync';\""
```
Expected: `{"state":"ok","since":"<iso ≥ Step 10.5 time>",...}`.

---

## Task 11: Close out the coordinator log

- [ ] **Step 11.1: zmem write-back — VERIFIED**

Run:
```bash
~/.claude/bin/zmem add \
  --title "n8n mesh deployment — VERIFIED" \
  --kind capture \
  --scope cli-claude-code \
  --source claude-code \
  --tags '{"phase":"deployment","status":"VERIFIED","claim":"55156A3894FC5224E063545E000AA403","wf_id":"<WF_ID>","branch":"spec/n8n-mesh-deployment"}' \
  "All four verify-first checks green: (1) auth/shape 200 + {count,results}; (2) onError item carries item.json.error; (3) classify ok + staticData persisted across two runs; (4) Slack delivery 200. Workflow active:true. e2e down→recovered exercised; two Slack messages landed; steady state back to ok."
```
Expected: new id; record it.

- [ ] **Step 11.2: zmem write-back — HANDOFF_COMPLETE**

Run:
```bash
~/.claude/bin/zmem add \
  --title "n8n mesh deployment — HANDOFF_COMPLETE" \
  --kind decision \
  --scope cli-claude-code \
  --source claude-code \
  --tags '{"phase":"deployment","status":"HANDOFF_COMPLETE","claim":"55156A3894FC5224E063545E000AA403","wf_id":"<WF_ID>","branch":"spec/n8n-mesh-deployment"}' \
  "Memory Mesh Sync live on avm-02. 15m schedule running. Alerts via Slack webhook in /opt/n8n/.env. Out of scope: reconcile/retire workflow-mem-memory-core-sync, Block Kit formatting, Prometheus export, NATS publication, Cloudflare expose of webhook arm. Rollback card: see docs/superpowers/plans/2026-06-25-n8n-mesh-deployment.md Task 13."
```

- [ ] **Step 11.3: Close the SSH tunnel**

In the tunnel terminal: `Ctrl+C`. Verify:
```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:5678/healthz 2>&1 || echo "tunnel_closed"
```
Expected: `tunnel_closed` (or `000`).

---

## Task 12: Finish the development branch

- [ ] **Step 12.1: Invoke the finishing-a-development-branch skill**

Use `superpowers:finishing-a-development-branch`. Run the unit-test gate first:
```bash
cd /Users/z/src/memory-core-mcp && node --test n8n/lib/
```
Expected: `# pass 30` `# fail 0`.

Then present the four-option menu (Merge locally / Push and create PR / Keep as-is / Discard). For this branch the recommendation is **Option 2 (Push and create PR)** — the workflow is deployed and merging to `main` records the auth correction in the source of truth.

- [ ] **Step 12.2: If Option 2 chosen — create the PR**

Run:
```bash
cd /Users/z/src/memory-core-mcp && git push -u origin spec/n8n-mesh-deployment && gh pr create --title "fix(mesh-sync): wire httpHeaderAuth credential; deploy heartbeat arm on avm-02" --body "$(cat <<'EOF'
## Summary
- Switches Memory Mesh Sync HTTP nodes (Store -> Mesh, Recent Pulse) from a manual Authorization: Bearer {{ \$env.ZMEMORY_BEARER_TOKEN }} header to authentication: genericCredentialType / httpHeaderAuth with the existing live credential 'Memory Gateway Bearer' (id MemGwBearer00001).
- Drops the dangling ZMEMORY_BEARER_TOKEN reference, which was never wired into /opt/n8n/.env on avm-02 and would have caused every /query poll to 401.
- Deployed to avm-02 (queue mode, n8n v2.22.4) inactive → verified four checks (auth/shape, onError item shape, classify + staticData persistence, Slack delivery) → activated; e2e down→recovered exercised; two Slack messages confirmed.

## Test plan
- [x] node --test n8n/lib/ — 30/30 pass (unchanged from PR #2)
- [x] n8n import:workflow on avm-02 — Successfully imported 1 workflow
- [x] Verify #1: Recent Pulse → 200 + {count,results}
- [x] Verify #2: temp bad URL → item.json.error present
- [x] Verify #3: 2 runs → staticData.meshHeartbeat persisted across executions
- [x] Verify #4: Notify Slack → 200, message in channel
- [x] Activate → 15-minute schedule registered
- [x] e2e: stop gateway → 🔴 DOWN; restart → ✅ RECOVERED; steady-state ok with no extra alerts
EOF
)"
```
Expected: PR URL printed. Return it to the user.

---

## Task 13: Rollback reference card (do not execute unless rolling back)

If at any point this deployment needs to be undone:

- [ ] **Instant rollback** — stop the schedule:
```bash
ssh z-agentic-vm-02 "sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T n8n n8n update:workflow --id=<WF_ID> --active=false"
```

- [ ] **Full rollback** — delete the workflow + restore `.env`:
```bash
ssh z-agentic-vm-02 "sudo -n docker compose -f /opt/n8n/docker-compose.yml exec -T n8n n8n delete:workflow --id=<WF_ID> && sudo -n cp -a /opt/n8n/.env.bak.2026-06-25 /opt/n8n/.env && cd /opt/n8n && sudo -n docker compose restart n8n n8n-worker"
```

- [ ] **Never touched during rollback:** `N8N_ENCRYPTION_KEY`, the `Memory Gateway Bearer` credential, the pre-existing `workflow-mem-memory-core-sync` workflow, Postgres data volume.

---

## Self-Review

**1. Spec coverage:**
- §1 decisions (Slack incoming webhook, reuse credential, drop env bearer, verify-first, inline execution) → Task 2 (auth correction), Task 3 (env wiring), Tasks 5–8 (verify-first), header note (inline execution).
- §3 one code change (both gateway nodes → `genericCredentialType`/`httpHeaderAuth` + cred `MemGwBearer00001`, drop Authorization header, keep Content-Type / User-Agent / onError) → Task 2 Steps 2.1–2.2.
- §4 env wiring (2 lines via ssh, never echoed, restart) → Task 3 Steps 3.3–3.7.
- §5 verify-first activation sequence → Tasks 4–9 (import inactive → auth/shape → onError → classify+staticData → Slack delivery → activate).
- §6 rollback → Task 13.
- §7 notes (queue-mode static data, timezone, `workflow-mem-memory-core-sync` distinct, fallback to UI attach) → Task 4 Step 4.3 collision check, Task 4 Step 4.6 fallback, Task 7 Step 7.2 DB write-back proof.
- §8 testing (unit pre-flight; integration = §5 verify; e2e = post-activation transition) → Task 1 Step 1.2 (unit), Tasks 5–8 (integration), Task 10 (e2e).
- §9 out of scope → recorded in Task 11 Step 11.2 HANDOFF_COMPLETE body.

**2. Placeholder scan:** No "TBD", "implement later", or "appropriate error handling" left. The only deliberate runtime substitution is `<WF_ID>` (captured at Task 4 Step 4.5 and reused with the exact same identifier in Tasks 9, 11, 12, 13), and `<PASTE_URL>` for the Slack webhook (handled by the human-operator hand-off pattern with explicit `read -r -s` so the value never enters the agent transcript).

**3. Type / identifier consistency:**
- Credential id `MemGwBearer00001` used identically in Steps 2.1, 2.2, 2.5, 4.6, and PR body.
- Credential name `Memory Gateway Bearer` used identically throughout.
- Workflow name `Memory Mesh Sync` used identically in Tasks 4 (collision check, import, list, DB), 5 (UI), 9 (DB), 10 (DB), 11 (zmem tags), 12 (PR body).
- Env keys `ZMEMORY_GATEWAY_URL` and `SLACK_MESH_ALERT_WEBHOOK` spelled identically in Tasks 3.1, 3.3, 3.4, 3.5, 3.7, and match `{{ $env.* }}` references unchanged in the workflow JSON.
- `WF_ID` is the single canonical identifier from Task 4 Step 4.5 onward.
- Coordinator phase labels (`STARTED`/`DECISION_LOCKED`/`MUTATION_DONE`/`VERIFIED`/`HANDOFF_COMPLETE`) match the introductory checkpoint list. Note: this plan collapses `MUTATION_DONE` into the `VERIFIED` write-back at Task 11.1 because the mutation (Task 3) and verification (Tasks 5–10) form a single inline gate — recording an intermediate `MUTATION_DONE` between them adds no audit value once verification has run.

No issues found.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-25-n8n-mesh-deployment.md`.

**Only one execution option for this plan: Inline Execution** via `superpowers:executing-plans`. Subagent-driven execution is explicitly disallowed in the header — steps touch live prod, the production `.env`, secrets over ssh, and an operator-controlled Slack webhook hand-off; delegation breaks the secret-handling guarantee.

Confirm to proceed and I'll invoke `superpowers:executing-plans` against this file.
