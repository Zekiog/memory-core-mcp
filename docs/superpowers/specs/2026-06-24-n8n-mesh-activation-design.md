# Design — n8n Memory Mesh Sync: Heartbeat Arm

- **Date:** 2026-06-24
- **Repo:** `memory-core-mcp`
- **Workflow:** `n8n/workflows/mesh_sync_workflow.json` (`Memory Mesh Sync`)
- **Status:** design approved (brainstorming) → input to `writing-plans`
- **Scope:** **workflow-first** — design the schedule arm's terminal node (currently `NoOp "Route To Clients"`). Deployment/activation on avm-02 is a **separate follow-up cycle**, not covered here.

## 1. Problem & decisions

The `Memory Mesh Sync` workflow has two arms. The **webhook ingest arm** is well-defined and unchanged. The **schedule arm** (`Every 15m → /query → NoOp`) ends in an undesigned NoOp. The mesh clients (Desktop/CLI/Pi) already query the gateway directly via `mesh_router`, so a 15-minute re-pull only earns its keep as **operational monitoring** — nothing else in the fleet detects a silently-dead gateway or a stalled ingest path.

Decisions locked during brainstorming:

| Dimension | Decision |
|---|---|
| Scope | Workflow-first (design the arm; deploy later) |
| Schedule arm purpose | Heartbeat + staleness alert |
| Alert sink | n8n self-contained → Slack (no new infra) |
| Alert cadence | Transition + recovery, **stateful** via `$getWorkflowStaticData('global')` |
| Implementation | Approach A — compact Code node, 3-state (`ok`/`stale`/`down`) |

## 2. Architecture

**Webhook ingest arm — UNCHANGED:**
```
Client Event In (webhook POST /mesh-sync)
  → Store → Mesh (HTTP /ingest, Bearer)
  → Respond
```

**Heartbeat arm — REDESIGNED (replaces NoOp):**
```
Every 15m (ScheduleTrigger)
  → Recent Pulse (HTTP POST /query {limit:1}, continueOnFail)
  → Evaluate Heartbeat (Code: classify + decide + static-data state)
  → Alert? (IF alert === true)
        ├─ true  → Notify Slack (HTTP POST incoming-webhook, retryOnFail)
        └─ false → (end, no message)
```

Two refinements vs. the current JSON:
- **`/query` limit `10` → `1`.** Heartbeat only needs the newest record's age.
- **`continueOnFail: true` on Recent Pulse.** A gateway error must not abort the run; the Code node classifies it as `down`. This is the technical basis for distinguishing **`down`** (gateway unreachable) from **`stale`** (gateway alive, ingest stopped).

**Network correctness:** n8n and the gateway both live on avm-02 (`10.10.0.2`). These calls are **in-mesh/host-local**, so they bypass the Cloudflare edge — the WAF default-UA 403 gotcha from `memory-mesh-integration-2026-06-23` does **not** apply to this arm. A defensive product `User-Agent` header is still set (cheap). Consequence: for n8n, `ZMEMORY_GATEWAY_URL = http://10.10.0.2:8848` (in-mesh), **not** the public edge.

## 3. State machine & data flow

Two pure functions (no n8n dependencies → unit-testable):

- **`classify(httpItem, now, thresholdMin) → 'ok' | 'stale' | 'down'`**
  - HTTP error / non-2xx / no usable body ⇒ `down`
  - success but records empty / newest record has no timestamp ⇒ `stale`
  - `age(newestTs, now) > thresholdMin` ⇒ `stale`; otherwise ⇒ `ok`
- **`decide(prev, current) → { alert, severity }`**
  - `prev` defaults to `ok` on first run (healthy cold start stays silent; a bad first run alerts — desired)
  - `current === prev` ⇒ `{ alert: false }`
  - else `current === 'ok'` ⇒ `{ alert: true, severity: 'recovered' }`
  - else ⇒ `{ alert: true, severity: current }`  // `stale` | `down`

**Transition table:**

| prev → current | action |
|---|---|
| ok → ok | silent |
| ok → stale | 🟠 alert (stale) |
| ok → down | 🔴 alert (down) |
| stale → ok | ✅ recovered |
| down → ok | ✅ recovered |
| stale → down | 🔴 alert (escalation) |
| down → stale | 🟠 alert (partial: gateway back, ingest still stopped) |
| stale → stale / down → down | silent |

**Static data:** `staticData.meshHeartbeat = { state, since, lastNewestTs }`. The Code node reads it, computes `current`, writes the new state + transition timestamp, and emits one item: `{ alert, severity, title, text, ageMin, newestTs }`. The IF node gates `alert === true` into the Slack node.

## 4. Alert payloads

Slack incoming-webhook, text MVP (no Block Kit yet):
- **down:** `🔴 *Memory Mesh DOWN* — gateway /query unreachable (avm-02:8848). Last healthy: <since>. Execution: <id>`
- **stale:** `🟠 *Memory Mesh STALE* — newest record <ageMin>m old (threshold <thresholdMin>m). Gateway up; ingest may have stopped.`
- **recovered:** `✅ *Memory Mesh RECOVERED* — flow normal. Newest record <ageMin>m old. Downtime ≈ <downtime>.`

## 5. Error handling

- **Recent Pulse:** `continueOnFail` ⇒ failure classified as `down` (no abort).
- **Notify Slack:** `retryOnFail`, `maxTries: 2`, 5s between. Final failure ⇒ n8n execution-error log (no loop).
- **Known limitation:** state is committed in the Code node *before* the Slack call. If a transition's Slack delivery fails, the alert is lost for that transition (n8n execution-error is the backstop). Acceptable for MVP; a future iteration can move the state commit to a post-Slack Set node.
- **First run:** `prev = ok` (handled in `decide`).
- **Unparseable timestamp:** treated as `stale` (data present, freshness unverifiable) + logged.
- **Clock/tz:** compare in UTC; gateway timestamps assumed UTC ISO (verify — see §7).

## 6. Parameters & defaults

| Param | Default | Where |
|---|---|---|
| `STALE_THRESHOLD_MIN` | `30` (2 poll cycles) | labeled const at top of Code node (tunable) |
| Poll cadence | `15m` | ScheduleTrigger (unchanged) |
| `/query` body | `{ limit: 1 }` | Recent Pulse |
| Gateway URL | `http://10.10.0.2:8848` | `{{$env.ZMEMORY_GATEWAY_URL}}` (in-mesh) |
| Slack webhook | secret | `{{$env.SLACK_MESH_ALERT_WEBHOOK}}` (provisioned in deployment cycle) |

## 7. Open items to verify (writing-plans inputs)

1. `/query` response JSON shape + newest-record timestamp field name + timezone — read gateway `src/`.
2. Gateway address reachable from the n8n **container** (`10.10.0.2:8848` via host vs docker-host gateway vs localhost).
3. n8n v2.22.4 httpRequest (typeVersion 4.2): exact param for continue-on-error (`continueOnFail` vs `onError: continueRegularOutput`) and the error item shape.
4. Slack incoming-webhook provisioning (channel + URL secret).

### Verified 2026-06-24

Checked against the gateway source in this repo (`src/memory_core/`) and the local working tree on `spec/n8n-mesh-heartbeat`:

- **`/query` response envelope — CONFIRMED.** `_http_query` (`src/memory_core/server.py:228`) returns `JSONResponse({"count": len(safe), "results": safe})`. The records live under the **`results`** key, which is already in `extractRecords`'s probe list (`records|data|results|items`). No code change needed.
- **Newest-record timestamp field — CONFIRMED.** Each record is serialized by the row mapper in `src/memory_core/db.py:203-205`, which iterates `("created_at", "updated_at")` and replaces each non-null value with `value.isoformat()`. The `_SELECT` list (`db.py:210-211`) exposes `created_at` and `updated_at`. The primary freshness field is **`created_at`**, which is the first entry in `TS_FIELDS` (`created_at|createdAt|inserted_at|ts|timestamp`). No code change needed.
- **Timezone — PARTIALLY CONFIRMED.** Values come from Oracle `created_at`/`updated_at` columns rendered via Python `datetime.isoformat()`. If the DB returns tz-aware datetimes the ISO string carries an offset (parseable by `Date.parse`); if naive, the offset is absent and JS interprets it as local time — on a negative-UTC-offset host this can place the parsed moment *after* `now`, yielding a negative age. `classify` now treats any negative/future age (and an unparseable or absent timestamp) as **`stale`**, which closes the previously-possible false-`ok` gap. The live wire timezone offset on avm-02 remains **unverified** (the gateway was not queried from this machine), but it no longer affects safety: the worst case degrades to a `stale` signal, never a missed one.
- **n8n continue-on-error property — CONFIRMED (by version, not by live instance).** `/opt/n8n` is a remote host and is **not** present locally (grep returned nothing, as expected). No repo workflow JSON uses either `onError` or `continueOnFail`, so there is no in-repo precedent to copy. The plan uses the n8n v2.x node-level form `"onError": "continueRegularOutput"`, which is correct for v2.22.4. Could **not** confirm against a running instance; if the deployed instance rejects it, fall back to legacy `"continueOnFail": true` on `Recent Pulse` during the deployment cycle.
- **Gateway reachability from the n8n container (open item #2)** — out of scope for this workflow-first cycle; deferred to the deployment cycle.

## 8. Testing (TDD)

- **Unit (`node --test`, no new deps):** RED first.
  - `classify`: fresh → ok; older-than-threshold → stale; empty → stale; http-error → down; boundary (exactly threshold) → ok; unparseable ts → stale.
  - `decide`: full transition table + first-run cases (undefined prev + down ⇒ alert; undefined prev + ok ⇒ silent).
- **Integration (manual, deployment cycle):** pin `Recent Pulse` with 4 fixtures (fresh / stale / empty / error); run sequentially and assert Slack fires only on transitions.

## 9. Out of scope (follow-up cycles)

- Deployment/activation on avm-02 (import, env wiring, `active: true`, verify, rollback).
- Block Kit / richer Slack formatting.
- Exporting `mesh_last_record_age` to Prometheus/Grafana (was a considered alert path; deferred).
- NATS event publication for mesh consumers.

## Links

- `~/.claude/.../memory/n8n-mesh-activation-status-2026-06-24.md`
- `~/.claude/.../memory/memory-mesh-integration-2026-06-23.md`
- `~/.claude/.../memory/project_n8n_avm02.md`
