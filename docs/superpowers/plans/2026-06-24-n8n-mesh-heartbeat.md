# n8n Memory Mesh Heartbeat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `Memory Mesh Sync` schedule arm's `NoOp "Route To Clients"` with a self-contained 3-state (ok/stale/down) heartbeat that posts transition + recovery alerts to Slack.

**Architecture:** The heartbeat logic lives as pure, unit-tested JS functions in `n8n/lib/heartbeat.mjs` (single source of truth). The workflow's `Evaluate Heartbeat` Code node inlines those functions verbatim plus a ~6-line n8n adapter (n8n Code nodes cannot import local modules). State persists across executions via `$getWorkflowStaticData('global')`. Deployment/activation on avm-02 is a **separate cycle** — out of scope here.

**Tech Stack:** Node.js built-in test runner (`node --test`, no new deps), n8n v2.22.4 (Code node typeVersion 2, httpRequest typeVersion 4.2, if typeVersion 2).

**Spec:** `docs/superpowers/specs/2026-06-24-n8n-mesh-activation-design.md`

---

## File Structure

- **Create** `n8n/lib/heartbeat.mjs` — pure functions: `extractRecords`, `extractNewestTs`, `classify`, `decide`, `buildHeartbeatItem`, `STALE_THRESHOLD_MIN`. Single source of truth.
- **Create** `n8n/lib/heartbeat.test.mjs` — `node --test` unit suite covering all functions + the transition table.
- **Modify** `n8n/workflows/mesh_sync_workflow.json` — schedule arm only: `Recent Pulse` (limit→1, continue-on-error), replace NoOp with `Evaluate Heartbeat` (Code) → `Alert?` (IF) → `Notify Slack` (HTTP). Webhook arm untouched.

---

## Task 1: Verify open items against the live gateway

**Files:**
- Read: gateway source under `src/` (query route handler)

- [ ] **Step 1: Find and read the `/query` handler**

Run:
```bash
cd /Users/z/src/memory-core-mcp
grep -rni "query" src/ --include='*.py' -l
grep -rni "created_at\|inserted_at\|timestamp\|\"ts\"\|created" src/ --include='*.py' | head -40
```
Goal: confirm (a) the `/query` JSON response envelope (bare array vs `{records|data|results|items: [...]}`), and (b) the per-record timestamp field name + timezone.

Note: `extractRecords`/`extractNewestTs` (Task 2) already probe `records|data|results|items` envelopes and `created_at|createdAt|inserted_at|ts|timestamp` fields, so a confirmed match means no code change. **Only if** the real field/envelope is outside those lists, add it to the constants in `heartbeat.mjs` in Task 2.

- [ ] **Step 2: Confirm the n8n continue-on-error parameter name for v2.22.4**

Run:
```bash
grep -rn "onError\|continueOnFail" /opt/n8n 2>/dev/null | head -5 || echo "check via n8n editor: node settings → 'On Error' → 'Continue (using error output)'"
```
Expected: n8n v2.x uses the node-level property `"onError": "continueRegularOutput"`. The plan's JSON (Task 6) uses that form. If this instance still uses the legacy `"continueOnFail": true`, substitute it on the `Recent Pulse` node in Task 6.

- [ ] **Step 3: Record findings as a checklist comment in the spec**

Append a short "Verified 2026-06-24" note under spec §7 with: response envelope shape, timestamp field name, and the confirmed continue-on-error property. No code yet.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-06-24-n8n-mesh-activation-design.md
git commit -m "docs: record verified gateway /query shape + n8n error param"
```

---

## Task 2: `extractRecords` + `extractNewestTs`

**Files:**
- Create: `n8n/lib/heartbeat.mjs`
- Test: `n8n/lib/heartbeat.test.mjs`

- [ ] **Step 1: Write the failing tests**

Create `n8n/lib/heartbeat.test.mjs`:
```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extractRecords, extractNewestTs } from './heartbeat.mjs';

test('extractRecords: bare array', () => {
  assert.deepEqual(extractRecords([{ a: 1 }]), [{ a: 1 }]);
});
test('extractRecords: records envelope', () => {
  assert.deepEqual(extractRecords({ records: [{ a: 1 }] }), [{ a: 1 }]);
});
test('extractRecords: data envelope', () => {
  assert.deepEqual(extractRecords({ data: [{ a: 1 }] }), [{ a: 1 }]);
});
test('extractRecords: none / null', () => {
  assert.deepEqual(extractRecords({ foo: 1 }), []);
  assert.deepEqual(extractRecords(null), []);
});
test('extractNewestTs: picks max created_at', () => {
  const body = { records: [
    { created_at: '2026-06-24T10:00:00Z' },
    { created_at: '2026-06-24T11:00:00Z' },
  ] };
  assert.equal(extractNewestTs(body), Date.parse('2026-06-24T11:00:00Z'));
});
test('extractNewestTs: alt field ts', () => {
  assert.equal(extractNewestTs([{ ts: '2026-06-24T09:00:00Z' }]), Date.parse('2026-06-24T09:00:00Z'));
});
test('extractNewestTs: empty -> null', () => {
  assert.equal(extractNewestTs({ records: [] }), null);
});
test('extractNewestTs: unparseable -> null', () => {
  assert.equal(extractNewestTs([{ created_at: 'not-a-date' }]), null);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/src/memory-core-mcp && node --test n8n/lib/heartbeat.test.mjs`
Expected: FAIL — `Cannot find module './heartbeat.mjs'`.

- [ ] **Step 3: Write minimal implementation**

Create `n8n/lib/heartbeat.mjs`:
```js
export const STALE_THRESHOLD_MIN = 30; // 2 poll cycles (poll = 15m). Tunable.
const TS_FIELDS = ['created_at', 'createdAt', 'inserted_at', 'ts', 'timestamp'];

export function extractRecords(body) {
  if (Array.isArray(body)) return body;
  if (body && typeof body === 'object') {
    for (const k of ['records', 'data', 'results', 'items']) {
      if (Array.isArray(body[k])) return body[k];
    }
  }
  return [];
}

export function extractNewestTs(body) {
  let newest = null;
  for (const rec of extractRecords(body)) {
    for (const f of TS_FIELDS) {
      if (rec && rec[f] != null) {
        const ms = Date.parse(rec[f]);
        if (!Number.isNaN(ms) && (newest === null || ms > newest)) newest = ms;
        break;
      }
    }
  }
  return newest;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test n8n/lib/heartbeat.test.mjs`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add n8n/lib/heartbeat.mjs n8n/lib/heartbeat.test.mjs
git commit -m "feat(heartbeat): record extraction + newest-timestamp helpers"
```

---

## Task 3: `classify`

**Files:**
- Modify: `n8n/lib/heartbeat.mjs`
- Test: `n8n/lib/heartbeat.test.mjs`

- [ ] **Step 1: Append failing tests**

Append to `n8n/lib/heartbeat.test.mjs`:
```js
import { classify } from './heartbeat.mjs';

const NOW = Date.parse('2026-06-24T12:00:00Z');

test('classify: fresh record -> ok', () => {
  assert.equal(classify(false, [{ created_at: '2026-06-24T11:50:00Z' }], NOW, 30), 'ok');
});
test('classify: old record -> stale', () => {
  assert.equal(classify(false, [{ created_at: '2026-06-24T11:00:00Z' }], NOW, 30), 'stale');
});
test('classify: boundary (exactly threshold) -> ok', () => {
  assert.equal(classify(false, [{ created_at: '2026-06-24T11:30:00Z' }], NOW, 30), 'ok');
});
test('classify: empty body -> stale', () => {
  assert.equal(classify(false, { records: [] }, NOW, 30), 'stale');
});
test('classify: http failed -> down', () => {
  assert.equal(classify(true, null, NOW, 30), 'down');
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test n8n/lib/heartbeat.test.mjs`
Expected: FAIL — `classify is not exported` / `is not a function`.

- [ ] **Step 3: Implement**

Append to `n8n/lib/heartbeat.mjs`:
```js
export function classify(httpFailed, body, nowMs, thresholdMin = STALE_THRESHOLD_MIN) {
  if (httpFailed) return 'down';
  const t = extractNewestTs(body);
  if (t === null) return 'stale';
  return (nowMs - t) / 60000 > thresholdMin ? 'stale' : 'ok';
}
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test n8n/lib/heartbeat.test.mjs`
Expected: PASS (13 tests total).

- [ ] **Step 5: Commit**

```bash
git add n8n/lib/heartbeat.mjs n8n/lib/heartbeat.test.mjs
git commit -m "feat(heartbeat): classify ok/stale/down"
```

---

## Task 4: `decide` (transition table)

**Files:**
- Modify: `n8n/lib/heartbeat.mjs`
- Test: `n8n/lib/heartbeat.test.mjs`

- [ ] **Step 1: Append failing tests**

Append to `n8n/lib/heartbeat.test.mjs`:
```js
import { decide } from './heartbeat.mjs';

test('decide: ok->ok silent', () => assert.deepEqual(decide('ok', 'ok'), { alert: false, severity: null }));
test('decide: ok->stale', () => assert.deepEqual(decide('ok', 'stale'), { alert: true, severity: 'stale' }));
test('decide: ok->down', () => assert.deepEqual(decide('ok', 'down'), { alert: true, severity: 'down' }));
test('decide: stale->ok recovered', () => assert.deepEqual(decide('stale', 'ok'), { alert: true, severity: 'recovered' }));
test('decide: down->ok recovered', () => assert.deepEqual(decide('down', 'ok'), { alert: true, severity: 'recovered' }));
test('decide: stale->down escalation', () => assert.deepEqual(decide('stale', 'down'), { alert: true, severity: 'down' }));
test('decide: down->stale partial', () => assert.deepEqual(decide('down', 'stale'), { alert: true, severity: 'stale' }));
test('decide: stale->stale silent', () => assert.deepEqual(decide('stale', 'stale'), { alert: false, severity: null }));
test('decide: first run undefined+ok silent', () => assert.deepEqual(decide(undefined, 'ok'), { alert: false, severity: null }));
test('decide: first run undefined+down alert', () => assert.deepEqual(decide(undefined, 'down'), { alert: true, severity: 'down' }));
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test n8n/lib/heartbeat.test.mjs`
Expected: FAIL — `decide is not a function`.

- [ ] **Step 3: Implement**

Append to `n8n/lib/heartbeat.mjs`:
```js
export function decide(prev, current) {
  const previous = prev || 'ok';
  if (current === previous) return { alert: false, severity: null };
  if (current === 'ok') return { alert: true, severity: 'recovered' };
  return { alert: true, severity: current };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test n8n/lib/heartbeat.test.mjs`
Expected: PASS (23 tests total).

- [ ] **Step 5: Commit**

```bash
git add n8n/lib/heartbeat.mjs n8n/lib/heartbeat.test.mjs
git commit -m "feat(heartbeat): decide transition + recovery alerts"
```

---

## Task 5: `buildHeartbeatItem` (driver + Slack text)

**Files:**
- Modify: `n8n/lib/heartbeat.mjs`
- Test: `n8n/lib/heartbeat.test.mjs`

- [ ] **Step 1: Append failing tests**

Append to `n8n/lib/heartbeat.test.mjs`:
```js
import { buildHeartbeatItem } from './heartbeat.mjs';

const T = Date.parse('2026-06-24T12:00:00Z');

test('build: ok->ok no alert, keeps since', () => {
  const out = buildHeartbeatItem({ httpFailed: false, body: [{ created_at: '2026-06-24T11:55:00Z' }], nowMs: T, prevState: 'ok', prevSince: '2026-06-24T09:00:00Z' });
  assert.equal(out.alert, false);
  assert.equal(out.current, 'ok');
  assert.equal(out.text, null);
  assert.equal(out.nextState, 'ok');
  assert.equal(out.nextSince, '2026-06-24T09:00:00Z');
});
test('build: ok->down alerts + new since', () => {
  const out = buildHeartbeatItem({ httpFailed: true, body: null, nowMs: T, prevState: 'ok', prevSince: '2026-06-24T09:00:00Z' });
  assert.equal(out.alert, true);
  assert.equal(out.severity, 'down');
  assert.match(out.text, /DOWN/);
  assert.equal(out.nextState, 'down');
  assert.equal(out.nextSince, '2026-06-24T12:00:00Z');
});
test('build: down->ok recovered with downtime', () => {
  const out = buildHeartbeatItem({ httpFailed: false, body: [{ created_at: '2026-06-24T11:59:00Z' }], nowMs: T, prevState: 'down', prevSince: '2026-06-24T11:00:00Z' });
  assert.equal(out.severity, 'recovered');
  assert.match(out.text, /RECOVERED/);
  assert.match(out.text, /Downtime ≈ 60m/);
});
test('build: stale alert reports age', () => {
  const out = buildHeartbeatItem({ httpFailed: false, body: [{ created_at: '2026-06-24T11:00:00Z' }], nowMs: T, prevState: 'ok', prevSince: null });
  assert.equal(out.severity, 'stale');
  assert.match(out.text, /60m old/);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test n8n/lib/heartbeat.test.mjs`
Expected: FAIL — `buildHeartbeatItem is not a function`.

- [ ] **Step 3: Implement**

Append to `n8n/lib/heartbeat.mjs`:
```js
export function buildHeartbeatItem({ httpFailed, body, nowMs, prevState, prevSince, thresholdMin = STALE_THRESHOLD_MIN }) {
  const previous = prevState || 'ok';
  const current = classify(httpFailed, body, nowMs, thresholdMin);
  const { alert, severity } = decide(previous, current);
  const newestMs = httpFailed ? null : extractNewestTs(body);
  const ageMin = newestMs === null ? null : Math.round((nowMs - newestMs) / 60000);
  const nowIso = new Date(nowMs).toISOString();
  const nextSince = current === previous ? (prevSince || nowIso) : nowIso;

  let text = null;
  if (alert) {
    if (severity === 'down') {
      text = `🔴 *Memory Mesh DOWN* — gateway /query unreachable (avm-02:8848). Last healthy: ${prevSince || 'unknown'}.`;
    } else if (severity === 'stale') {
      text = `🟠 *Memory Mesh STALE* — newest record ${ageMin}m old (threshold ${thresholdMin}m). Gateway up; ingest may have stopped.`;
    } else {
      const dt = prevSince ? `${Math.round((nowMs - Date.parse(prevSince)) / 60000)}m` : 'unknown';
      text = `✅ *Memory Mesh RECOVERED* — flow normal. Newest record ${ageMin ?? '?'}m old. Downtime ≈ ${dt}.`;
    }
  }

  return {
    alert,
    severity,
    current,
    ageMin,
    newestTs: newestMs === null ? null : new Date(newestMs).toISOString(),
    text,
    nextState: current,
    nextSince,
  };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test n8n/lib/heartbeat.test.mjs`
Expected: PASS (27 tests total).

- [ ] **Step 5: Commit**

```bash
git add n8n/lib/heartbeat.mjs n8n/lib/heartbeat.test.mjs
git commit -m "feat(heartbeat): buildHeartbeatItem driver + Slack payloads"
```

---

## Task 6: Wire the workflow JSON (schedule arm)

**Files:**
- Modify: `n8n/workflows/mesh_sync_workflow.json`

The webhook arm (`Client Event In` → `Store -> Mesh` → `Respond`) and its connections are **unchanged**. Only the schedule arm changes.

- [ ] **Step 1: Update `Recent Pulse` node**

In `n8n/workflows/mesh_sync_workflow.json`, replace the `http-poll` node object (currently `"name": "Recent Pulse"`) with:
```json
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
}
```

- [ ] **Step 2: Replace the `Route To Clients` NoOp with the `Evaluate Heartbeat` Code node**

Delete the `noop-route` node object and insert in its place:
```json
{
  "parameters": {
    "jsCode": "const STALE_THRESHOLD_MIN = 30;\nconst TS_FIELDS = ['created_at','createdAt','inserted_at','ts','timestamp'];\nfunction extractRecords(body){if(Array.isArray(body))return body;if(body&&typeof body==='object'){for(const k of ['records','data','results','items'])if(Array.isArray(body[k]))return body[k];}return [];}\nfunction extractNewestTs(body){let newest=null;for(const rec of extractRecords(body)){for(const f of TS_FIELDS){if(rec&&rec[f]!=null){const ms=Date.parse(rec[f]);if(!Number.isNaN(ms)&&(newest===null||ms>newest))newest=ms;break;}}}return newest;}\nfunction classify(httpFailed,body,nowMs,thresholdMin){if(httpFailed)return 'down';const t=extractNewestTs(body);if(t===null)return 'stale';return (nowMs-t)/60000>thresholdMin?'stale':'ok';}\nfunction decide(prev,current){const previous=prev||'ok';if(current===previous)return {alert:false,severity:null};if(current==='ok')return {alert:true,severity:'recovered'};return {alert:true,severity:current};}\nfunction buildHeartbeatItem(o){const previous=o.prevState||'ok';const current=classify(o.httpFailed,o.body,o.nowMs,o.thresholdMin||STALE_THRESHOLD_MIN);const d=decide(previous,current);const newestMs=o.httpFailed?null:extractNewestTs(o.body);const ageMin=newestMs===null?null:Math.round((o.nowMs-newestMs)/60000);const nowIso=new Date(o.nowMs).toISOString();const nextSince=current===previous?(o.prevSince||nowIso):nowIso;let text=null;if(d.alert){if(d.severity==='down'){text='🔴 *Memory Mesh DOWN* — gateway /query unreachable (avm-02:8848). Last healthy: '+(o.prevSince||'unknown')+'.';}else if(d.severity==='stale'){text='🟠 *Memory Mesh STALE* — newest record '+ageMin+'m old (threshold '+(o.thresholdMin||STALE_THRESHOLD_MIN)+'m). Gateway up; ingest may have stopped.';}else{const dt=o.prevSince?Math.round((o.nowMs-Date.parse(o.prevSince))/60000)+'m':'unknown';text='✅ *Memory Mesh RECOVERED* — flow normal. Newest record '+(ageMin==null?'?':ageMin)+'m old. Downtime ≈ '+dt+'.';}}return {alert:d.alert,severity:d.severity,current:current,ageMin:ageMin,newestTs:newestMs===null?null:new Date(newestMs).toISOString(),text:text,nextState:current,nextSince:nextSince};}\nconst sd=$getWorkflowStaticData('global');\nconst prev=sd.meshHeartbeat||{};\nconst item=$input.first();\nconst httpFailed=!!(item.json&&item.json.error);\nconst out=buildHeartbeatItem({httpFailed:httpFailed,body:httpFailed?null:item.json,nowMs:Date.now(),prevState:prev.state||'ok',prevSince:prev.since||null});\nsd.meshHeartbeat={state:out.nextState,since:out.nextSince,lastNewestTs:out.newestTs};\nreturn [{json:{alert:out.alert,severity:out.severity,current:out.current,ageMin:out.ageMin,text:out.text}}];"
  },
  "id": "evaluate-heartbeat",
  "name": "Evaluate Heartbeat",
  "type": "n8n-nodes-base.code",
  "typeVersion": 2,
  "position": [720, 540]
}
```

> The `jsCode` above is `heartbeat.mjs` inlined verbatim (minus `export`) plus the n8n adapter. If `heartbeat.mjs` changes, regenerate this string from it.

- [ ] **Step 3: Add the `Alert?` IF node and `Notify Slack` HTTP node**

Add these two node objects to the `nodes` array:
```json
{
  "parameters": {
    "conditions": {
      "options": { "caseSensitive": true, "typeValidation": "strict", "version": 2 },
      "conditions": [
        { "id": "alert-true", "leftValue": "={{ $json.alert }}", "rightValue": true, "operator": { "type": "boolean", "operation": "true", "singleValue": true } }
      ],
      "combinator": "and"
    },
    "options": {}
  },
  "id": "alert-gate",
  "name": "Alert?",
  "type": "n8n-nodes-base.if",
  "typeVersion": 2,
  "position": [960, 540]
},
{
  "parameters": {
    "method": "POST",
    "url": "={{ $env.SLACK_MESH_ALERT_WEBHOOK }}",
    "sendBody": true,
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ text: $json.text }) }}",
    "options": {}
  },
  "id": "notify-slack",
  "name": "Notify Slack",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "position": [1200, 540],
  "retryOnFail": true,
  "maxTries": 2,
  "waitBetweenTries": 5000
}
```

- [ ] **Step 4: Rewire the schedule-arm connections**

Replace the `"Every 15m"` and `"Recent Pulse"` connection entries, and remove the old `"Route To Clients"` reference, so the `connections` object's schedule arm reads:
```json
"Every 15m": {
  "main": [ [ { "node": "Recent Pulse", "type": "main", "index": 0 } ] ]
},
"Recent Pulse": {
  "main": [ [ { "node": "Evaluate Heartbeat", "type": "main", "index": 0 } ] ]
},
"Evaluate Heartbeat": {
  "main": [ [ { "node": "Alert?", "type": "main", "index": 0 } ] ]
},
"Alert?": {
  "main": [ [ { "node": "Notify Slack", "type": "main", "index": 0 } ], [] ]
}
```
(The webhook-arm connections — `Client Event In`, `Store -> Mesh` — stay exactly as they were.)

- [ ] **Step 5: Validate the JSON parses and node graph is correct**

Run:
```bash
cd /Users/z/src/memory-core-mcp
node -e "const w=require('./n8n/workflows/mesh_sync_workflow.json');const names=w.nodes.map(n=>n.name);console.log('nodes:',names.join(', '));if(names.includes('Route To Clients'))throw new Error('NoOp still present');for(const n of ['Evaluate Heartbeat','Alert?','Notify Slack'])if(!names.includes(n))throw new Error('missing '+n);if(w.active!==false)throw new Error('active must stay false');console.log('OK');"
```
Expected: prints the 8 node names and `OK` (no NoOp, three new nodes present, `active` still `false`).

- [ ] **Step 6: Commit**

```bash
git add n8n/workflows/mesh_sync_workflow.json
git commit -m "feat(heartbeat): wire schedule arm to staleness alert (Code+IF+Slack)"
```

---

## Task 7: Full suite + plan close-out

**Files:** none (verification only)

- [ ] **Step 1: Run the complete unit suite**

Run: `cd /Users/z/src/memory-core-mcp && node --test n8n/lib/`
Expected: PASS — 27 tests, 0 failures.

- [ ] **Step 2: Confirm the workflow stays inactive (activation is a separate cycle)**

Run: `node -e "console.log('active:', require('./n8n/workflows/mesh_sync_workflow.json').active)"`
Expected: `active: false`.

- [ ] **Step 3: Final commit (if any uncommitted changes remain)**

```bash
git status --short
# if clean, nothing to do
```

---

## Out of scope (follow-up cycles)

- Deployment/activation on avm-02 (import workflow, set `ZMEMORY_GATEWAY_URL`/`ZMEMORY_BEARER_TOKEN`/`SLACK_MESH_ALERT_WEBHOOK` in `/opt/n8n/.env`, `active: true`, verify, rollback).
- Slack incoming-webhook provisioning (channel + URL secret).
- Block Kit / richer formatting; Prometheus/Grafana metric export; NATS publication.
