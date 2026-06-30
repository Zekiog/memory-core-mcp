# Design — n8n Memory Mesh Sync: Deployment / Activation on avm-02

- **Date:** 2026-06-25
- **Repo:** `memory-core-mcp`
- **Branch:** `spec/n8n-mesh-deployment`
- **Workflow:** `n8n/workflows/mesh_sync_workflow.json` (`Memory Mesh Sync`)
- **Status:** design approved (brainstorming) → input to `writing-plans`
- **Predecessor:** [`2026-06-24-n8n-mesh-activation-design.md`](2026-06-24-n8n-mesh-activation-design.md) (heartbeat arm; merged via PR #2)
- **Scope:** Get the **heartbeat arm live on avm-02** — one workflow-JSON auth correction, env wiring, verify-first dry-run, `active:true`, e2e Slack test, rollback path. The heartbeat *logic* is already designed, built, unit-tested (30/30), and merged to `main`; this cycle is **deployment only**.

## 1. Problem & decisions

PR #2 landed the heartbeat arm (Code + IF + Slack) on `main`, but the workflow has **never been imported to avm-02** and is `active: false`. Pre-window recon (read-only, secret-safe) over `z-agentic-vm-02` retired most of the carried risks and surfaced one real gap the predecessor spec missed: **`/query` requires authentication**, and the workflow's gateway nodes authenticate via an env var that is **absent from `.env`** — so as-is, every poll would 401 and misclassify the gateway as `down` forever.

Decisions locked during brainstorming:

| Dimension | Decision | Rationale |
|---|---|---|
| Alert sink | **Slack incoming webhook** → `SLACK_MESH_ALERT_WEBHOOK` in `/opt/n8n/.env` | Faithful to predecessor spec; alert text is already Slack-formatted |
| Gateway auth | **Reuse existing credential `Memory Gateway Bearer` (`httpHeaderAuth`)**; drop the raw-token env var | Avoids storing a plaintext bearer in `.env`; reuses a vetted credential |
| Gateway URL | Add `ZMEMORY_GATEWAY_URL=http://10.10.0.2:8848` to `.env` (non-secret, in-mesh) | Both arms already reference `{{$env.ZMEMORY_GATEWAY_URL}}`; var is currently unset |
| Activation posture | **Verify-first → activate** | Burn down the last live risk (onError item shape) before going live |
| Execution method | **Inline (this session, gated)** — *not* subagent-driven | Steps touch live prod + secrets over ssh; unsafe to delegate |

## 2. Starting state (verified in-tree + recon)

**Workflow JSON (`mesh_sync_workflow.json`), both HTTP nodes today:**

- `Store -> Mesh` (webhook arm, line ~25) and `Recent Pulse` (heartbeat arm, line ~69) each send a manual header `Authorization: Bearer {{ $env.ZMEMORY_BEARER_TOKEN }}`.
- `Recent Pulse` already has `onError: continueRegularOutput` (line ~83) — **no change needed**.
- Both arms reference `{{ $env.ZMEMORY_GATEWAY_URL }}`; `Notify Slack` references `{{ $env.SLACK_MESH_ALERT_WEBHOOK }}`.
- `"active": false`.

**avm-02 (`/opt/n8n`, n8n v2.22.4, queue mode):**

- `.env` is **missing** `ZMEMORY_GATEWAY_URL`, `ZMEMORY_BEARER_TOKEN`, and `SLACK_MESH_ALERT_WEBHOOK` (key-name-only grep; values never read).
- Credential `Memory Gateway Bearer` (type `httpHeaderAuth`) **exists** in the live instance.
- Live `/query` returns **401** unauthenticated; `/healthz` returns `{"ok":true}`.
- **No** workflow named `Memory Mesh Sync` exists yet (first import). A pre-existing `workflow-mem-memory-core-sync` exists — see §7.
- Topology: `EXECUTIONS_MODE=queue` (main + worker + redis + postgres). Schedule triggers fire on **main**; execution runs on the **worker**.

## 3. The one code change (auth correction)

On `spec/n8n-mesh-deployment`, edit `mesh_sync_workflow.json` so both gateway nodes authenticate via the existing credential instead of the unset env bearer:

For **`Recent Pulse`** and **`Store -> Mesh`**:
- Remove the manual `{ "name": "Authorization", "value": "=Bearer {{ $env.ZMEMORY_BEARER_TOKEN }}" }` header.
- Add `"authentication": "genericCredentialType"`, `"genericAuthType": "httpHeaderAuth"`.
- Add a sibling `"credentials": { "httpHeaderAuth": { "id": "<LIVE_ID>", "name": "Memory Gateway Bearer" } }` block. `<LIVE_ID>` is fetched from the n8n DB during execution (a credential **id** is an opaque reference, not a secret — safe to commit).
- Keep the remaining headers: `Content-Type` on `Store -> Mesh`; `User-Agent` on `Recent Pulse`. Keep `onError` on `Recent Pulse`.

Consequence: `ZMEMORY_BEARER_TOKEN` is no longer referenced anywhere, so it is **not** added to `.env`. The webhook arm is dormant (nothing POSTs `/mesh-sync` yet); switching it to the credential is a one-node hygiene fix made while in the file — it changes no behavior but removes a latent 401.

The inlined `heartbeat.mjs` logic in `Evaluate Heartbeat` is **unchanged** (its `httpFailed = !!(item.json && item.json.error)` contract is what verify step §5.3 confirms).

## 4. Environment wiring (secret-safe, via ssh)

Append two lines to `/opt/n8n/.env`, then `docker compose restart` (from `/opt/n8n`). All writes go through ssh; **no value is ever echoed to the terminal**; verification is key-name + presence only.

| Var | Value | Secret? |
|---|---|---|
| `ZMEMORY_GATEWAY_URL` | `http://10.10.0.2:8848` | no |
| `SLACK_MESH_ALERT_WEBHOOK` | *(URL you provide)* | yes |

**Prerequisite (your side):** a Slack incoming-webhook URL. If one does not already exist, create it at `api.slack.com/apps` → *Incoming Webhooks* → pick the target channel. This is the one manual step that cannot be automated from here. The webhook URL is wired above and exercised in §5.5.

## 5. Activation sequence (verify-first)

Import **inactive**, verify all four checks, then activate. Dry-run inspection uses the SSH-tunnel UI: `ssh -L 5678:10.10.0.2:5678 z-agentic-vm-02` → `http://localhost:5678`.

1. **Import (inactive).** Copy the edited JSON into the n8n container and run `n8n import:workflow` (workflow's `active:false` is preserved). Confirm it appears as a distinct workflow (`Memory Mesh Sync`, webhook path `/mesh-sync`).
2. **Auth + response shape.** Execute `Recent Pulse` against live `/query` → expect **HTTP 200** and body `{ count, results }`. Proves the `Memory Gateway Bearer` credential injects a valid token (no 401).
3. **`onError` item shape (last live risk).** Temporarily point `Recent Pulse` at an unreachable target (e.g. `http://10.10.0.2:9/query`), run once → confirm the downstream item carries **`item.json.error`** (the exact property `Evaluate Heartbeat` keys on). Restore the URL afterward.
4. **Classify + state persistence.** With auth working, run the schedule path → `Evaluate Heartbeat` emits `current:'ok'`, `alert:false`. Run a **second** time → confirm `$getWorkflowStaticData('global').meshHeartbeat` carries the prior state forward (proves static data survives across executions in **queue mode**).
5. **Slack delivery.** Manually fire `Notify Slack` with a test payload (or force a transition) → confirm a message lands in the target channel via the new webhook.
6. **Activate.** All four green → set `active: true` (UI toggle or `n8n update:workflow --id <id> --active true`). The 15-minute schedule begins.

## 6. Rollback

This is a clean first import, so reverting returns avm-02 to its exact pre-deployment state:

- **Instant:** set `active: false` (UI or CLI) — schedule stops firing immediately.
- **Full:** delete the `Memory Mesh Sync` workflow; optionally remove the two `.env` lines and `docker compose restart`.
- `N8N_ENCRYPTION_KEY` and all existing credentials are **never touched** throughout.

## 7. Notes / correctness

- **Queue mode + static data.** Schedule fires on main; the worker executes the Code node and writes `staticData.meshHeartbeat` back to the workflow record in Postgres. The 15-minute cadence is serial (no overlapping runs) → no write race. Verified by §5.4.
- **Timezone.** avm-02 host + container = UTC; gateway co-located UTC; Code-node age math is epoch/UTC-safe; the `ageMin < 0 → stale` guard covers residual skew. Effectively eliminated.
- **Pre-existing `workflow-mem-memory-core-sync`.** A separate, possibly older mesh-sync workflow exists live. `Memory Mesh Sync` is distinct (different name, webhook path `/mesh-sync`). During import, confirm no collision; **reconciling or retiring the old workflow is out of scope** for this cycle.
- **Import method.** CLI `import:workflow` (file copied into the container) is the recommended path — reproducible, git is source of truth, and embedding the live credential id wires auth automatically. Fallback if CLI rejects the credential reference: import without it, then attach `Memory Gateway Bearer` to both nodes via the tunnel UI (manual, not git-tracked).

## 8. Testing

- **Unit:** already green and merged (`node --test`, 30/30 on `heartbeat.mjs`). The inline Code-node copy is byte-identical in logic; no re-run required, but `node --test n8n/lib/` is a cheap pre-flight.
- **Integration = the §5 verify-first dry-run** (auth/shape, onError item shape, classify + state persistence, Slack delivery). This is the deployment cycle's real test gate; activation is blocked until all four pass.
- **E2E:** post-activation, force one real transition (e.g. brief gateway stop in a maintenance window, or temporary threshold drop) and confirm a single Slack alert + one recovery message. Optional; only if a safe window exists.

## 9. Out of scope (follow-up)

- Reconciling / retiring `workflow-mem-memory-core-sync`.
- Block Kit / richer Slack formatting.
- Exporting `mesh_last_record_age` to Prometheus/Grafana.
- NATS event publication; public Cloudflare expose of the webhook arm.

## Links

- Predecessor spec: [`2026-06-24-n8n-mesh-activation-design.md`](2026-06-24-n8n-mesh-activation-design.md)
- `~/.claude/.../memory/n8n-mesh-activation-status-2026-06-24.md`
- `~/.claude/.../memory/project_n8n_avm02.md`
- `~/.claude/.../memory/memory-mesh-integration-2026-06-23.md`
