# Design — Mesh Gate: Continuous Assurance + Tamper-Aware Audit (avm-02 exposure)

- **Date:** 2026-06-29
- **Repo:** `memory-core-mcp`
- **Branch:** `spec/mesh-gate-continuous`
- **Status:** design approved (brainstorming) → input to `writing-plans`
- **Scope:** Turn the one-shot `gate_check.sh` (4-ADIM public-exposure check) into a **scheduled, continuously-asserted, alert-wired security sensor** for avm-02, plus a **machine-readable, append-only audit ledger**. avm-01 is the spine: it schedules the gate, exposes verdicts as Prometheus metrics, and alerts on RED / staleness / config-error.
- **Out of band:** This spec does **not** remediate avm-02. The gate is read-only; it observes and reports. P2.1 backstop *application* remains a separate, operator-gated action.

## 1. Problem & decisions

The 2026-06 verification produced a credible PASS: avm-02 has zero public exposure on 5678/6379/5432 (mesh/loopback binds only, UFW default-deny, external probe filtered). `gate_check.sh` (currently at `/Users/z/infra/gate/`, **unversioned**) encodes those 4 ADIM checks and returns GREEN/RED/CONFIG.

What the PASS does **not** prove: **(a)** it is a snapshot — a future bind change, a `docker run -p 0.0.0.0:...`, or a UFW edit could silently open exposure between checks; **(b)** the `DOCKER-USER` chain is empty (latent P2.1 Docker↔UFW bypass gap), currently masked only by IP-scoped binds. A snapshot says "no hole today," not "no hole tomorrow." This design closes the *temporal* gap and makes every verdict durable and tamper-evident.

Decisions locked during brainstorming:

| Dimension | Decision | Rationale |
|---|---|---|
| Track | **V1 continuous assurance + V2 tamper-aware audit** | Drift detection + a machine record are the two highest-leverage upgrades; everything else (fleet, threat-model, P2.1 apply) is a named follow-on |
| Substrate | **Prometheus textfile-collector → node_exporter → Prometheus → Alertmanager** | avm-01 already runs the full stack; n8n rejected as alert substrate because its `$getWorkflowStaticData('global')` does **not** persist across executions in n8n 2.22.4 → state-transition alerts silently suppressed |
| Topology | **avm-01 runs everything** | avm-01 (10.10.0.1) ssh's avm-02 for internal ADIM 1–3 over mesh, and probes avm-02's **public IP** for ADIM 4 — a genuine off-host external vantage. No dependency on the Mac being on |
| Scheduler | **systemd timer** (not cron) | Better journald logging, dependency ordering, `RandomizedDelaySec` jitter, `--check` testability |
| Failure stance | **Fail-closed** | An ADIM whose evidence can't be gathered (timeout, partial ssh) counts as **fail**, never silently as pass. Unknown ≠ safe for a security gate |
| Code home | **Move into `memory-core-mcp` under `security/gate/`** | Versions the currently-unversioned script (Default-1), co-locates with CI and existing specs |
| Audit rigor | **MVP = `security-audit.jsonl` append now**; hash-chain + ADB ship = named follow-on | YAGNI: the JSONL line is the unit of value; tamper-evidence and durable cross-host ship layer on top later (Default-2) |
| Alert channel | **Dedicated Slack `#security` webhook** | Don't overload the mesh-heartbeat webhook; security signals get their own sink |

## 2. Architecture overview

A pull-based continuous-assurance loop. `gate_check.sh` stays the **sensor** (same 4 ADIM, same RED/GREEN logic); four layers wrap it:

```
[systemd timer @ avm-01, every 15m + jitter]
   → gate_check.sh --format prom
        ├─ ssh 10.10.0.2 : ss / ufw / docker-user / docker-bind   (ADIM 1–3, internal, over mesh)
        └─ probe 79.76.57.223 : 5678,6379,5432 (+ :22 control)     (ADIM 4, external, off-host)
        → render prom → atomic write  <node_exporter textfile dir>/mesh_gate.prom
   → gate_check.sh --format json | append_jsonl  → security-audit.jsonl   (flock, append-only)
[node_exporter @ avm-01]  exposes textfile metrics
[Prometheus  @ avm-01]    scrapes node_exporter
[rules]                   mesh_gate_verdict==0 (5m) | stale (>1h) | config_error (15m)
[Alertmanager]            → Slack #security
[Grafana]                 verdict timeline + per-ADIM heatmap (optional panel)
```

Internal ADIM use the mesh IP (`10.10.0.2`) — fast, no public dependency. ADIM 4 uses the **public IP** (`79.76.57.223`) from avm-01 so the probe leaves avm-02's host boundary, which is what makes the "filtered" result meaningful. Both paths are reachable from avm-01 today.

## 3. Components (isolated units)

All under `security/gate/` in the repo. Each unit has one purpose and a defined interface.

| Unit | Purpose | Interface / depends on |
|---|---|---|
| `gate_check.sh` | The sensor. 4 ADIM, read-only, idempotent. Gains `FORMAT` output modes. | env (`TARGET_HOST`, `TARGET_PUB_IP`, `PORTS`, `STRICT_P2_1`, `PROBE_TOOL`, **`FORMAT`**); exit 0/1/2; stdout in chosen format. Depends on: ssh to avm-02, `nc`/`nmap` on runner |
| `systemd/mesh-gate.service` | One-shot unit: run gate `--format prom`, atomic-write textfile; run gate `--format json`, append ledger. | `Type=oneshot`; `ExecStart` wrapper; writes `.tmp`+`mv` |
| `systemd/mesh-gate.timer` | Cadence. `OnCalendar=*:0/15` + `RandomizedDelaySec`. | activates `.service` |
| `prometheus/mesh_gate.rules.yml` | Alert rules: RED, Stale, ConfigError. | dropped into Prometheus rules dir; Alertmanager routes to Slack |
| `audit/append_jsonl.sh` | Atomic, `flock`-guarded append of one JSON verdict line. | reads gate `--format json` on stdin; writes `security-audit.jsonl` |
| `tests/` | `bats` units: render golden-files + classify logic with mocked ss/ufw/docker/probe. | no live targets; pure fixtures |
| `README.md` | Deploy + rollback runbook (per-step `--check`). | operator-facing |

The existing human `security-audit.md` is **retained** for narrative milestones (the PASS report); the JSONL is the machine record. The two are not merged.

## 4. The one code change to the sensor: `--format` modes

Today `gate_check.sh` accumulates `FAILS[]`/`WARNS[]` and prints a single text line. The change: track **structured per-ADIM results** internally, then dispatch rendering by `FORMAT` (default `text`, so existing `tee -a security-audit.md` usage is unchanged).

Internal state to add (set by the four `stepN_*` functions):

- `ADIM_SS`, `ADIM_UFW`, `ADIM_DOCKER`, `ADIM_EXT` ∈ {0,1} (1 = pass)
- `PORT_OPEN["<port>"]` ∈ {0,1} (1 = externally reachable = bad)
- `EXEC_START` epoch (for duration)

Rendering is a separate function block (sensing must not be polluted by format concerns):

- `text` — the current single-line verdict (unchanged).
- `json` — one object (the ledger line, see §7).
- `prom` — Prometheus exposition format (see §5).

`STRICT_P2_1` semantics are unchanged: non-strict → empty DOCKER-USER is a `WARN` (latent), strict → it's a `FAIL` (RED). The flag value is surfaced as a metric so dashboards can distinguish "latent, accepted" from "RED".

## 5. Prometheus metric schema (`mesh_gate.prom`)

```
# HELP mesh_gate_verdict Overall gate verdict (1=GREEN no exposure, 0=RED exposure found)
# TYPE mesh_gate_verdict gauge
mesh_gate_verdict{host="avm-02"} 1
# HELP mesh_gate_adim Per-step result (1=pass, 0=fail)
# TYPE mesh_gate_adim gauge
mesh_gate_adim{host="avm-02",adim="ss"} 1
mesh_gate_adim{host="avm-02",adim="ufw"} 1
mesh_gate_adim{host="avm-02",adim="docker_bind"} 1
mesh_gate_adim{host="avm-02",adim="ext_probe"} 1
# HELP mesh_gate_ext_port_open External reachability (1=OPEN=bad, 0=closed/filtered=good)
# TYPE mesh_gate_ext_port_open gauge
mesh_gate_ext_port_open{host="avm-02",port="5678"} 0
mesh_gate_ext_port_open{host="avm-02",port="6379"} 0
mesh_gate_ext_port_open{host="avm-02",port="5432"} 0
# HELP mesh_gate_strict_p2_1 STRICT_P2_1 in effect (1=empty DOCKER-USER counts as RED)
# TYPE mesh_gate_strict_p2_1 gauge
mesh_gate_strict_p2_1{host="avm-02"} 0
# HELP mesh_gate_config_error Gate hit CONFIG error (exit 2 — target/tool unreachable)
# TYPE mesh_gate_config_error gauge
mesh_gate_config_error{host="avm-02"} 0
# HELP mesh_gate_exec_duration_seconds Wall time of the gate run
# TYPE mesh_gate_exec_duration_seconds gauge
mesh_gate_exec_duration_seconds{host="avm-02"} 2.4
# HELP mesh_gate_last_run_timestamp_seconds Unix time the gate completed
# TYPE mesh_gate_last_run_timestamp_seconds gauge
mesh_gate_last_run_timestamp_seconds{host="avm-02"} 1751193600
```

**Inverted-semantic warning (documented deliberately):** `mesh_gate_ext_port_open` is `1` when a port is OPEN, which is the *bad* state. Every consumer (alert, dashboard) must treat `==1` as the failure. This inversion is intentional (the metric names a fact — "is the port open" — not a verdict) and is called out here so no rule reads it backwards.

## 6. Alert rules

```yaml
groups:
  - name: mesh_gate
    rules:
      - alert: MeshGateRED
        expr: mesh_gate_verdict == 0
        for: 5m
        labels: { severity: critical }
        annotations:
          summary: "Mesh gate RED on {{ $labels.host }} — public exposure detected"
          description: "gate_check.sh verdict=RED. Inspect mesh_gate_adim / mesh_gate_ext_port_open."
      - alert: MeshGateStale
        expr: time() - mesh_gate_last_run_timestamp_seconds > 3600
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "Mesh gate sensor STALE on {{ $labels.host }} — no fresh run >1h (timer dead?)"
          description: "The security sensor stopped producing verdicts. A silent monitor is worse than none."
      - alert: MeshGateConfigError
        expr: mesh_gate_config_error == 1
        for: 15m
        labels: { severity: warning }
        annotations:
          summary: "Mesh gate CONFIG error on {{ $labels.host }} — ssh/tool broken, gate blind"
```

**Three-way separation (clean by construction):**

- Timer dead / script crash → `last_run_timestamp` goes stale → **MeshGateStale**.
- Script runs but ssh/probe tool broke → `config_error=1`, timestamp still fresh → **MeshGateConfigError**.
- Script runs, target reachable, hole found → `verdict=0` → **MeshGateRED**.

`MeshGateStale` is treated as near-critical in posture even though labelled `warning`: a security monitor that silently stops produces a false "all clear." As a belt-and-suspenders cross-check, `node_textfile_mtime_seconds{file="mesh_gate.prom"}` and `node_textfile_scrape_error` can back the staleness signal at deploy time.

## 7. Audit ledger (`security-audit.jsonl`) — V2 MVP

Each run appends exactly one line via `append_jsonl.sh` (under `flock`, append-only):

```json
{"ts":"2026-06-29T12:00:00Z","host":"avm-02","verdict":"GREEN","exit":0,"fails":[],"warns":["docker-user:empty(P2.1-latent)"],"ext_ports":{"5678":"closed","6379":"closed","5432":"closed"},"probe":"nc","strict_p2_1":0,"runner":"avm-01","duration_s":2.4}
```

- Lives on avm-01 at a stable path (e.g. `/var/lib/mesh-gate/security-audit.jsonl`; exact path confirmed at deploy).
- `flock` prevents interleaved appends if two runs ever overlap (the 15m serial cadence makes this defensive, not expected).

**Named follow-on (NOT in this spec):**

- **Hash-chain:** each record carries `prev_sha256` + `sha256` = `sha256(canonical(record\sha256) + prev_sha256)`. A broken chain = tampering. Tamper-evident, append-only ledger.
- **ADB ship:** `zmem add` (scope `security-gate`) each verdict, so history is queryable cross-host and survives loss of avm-01.

## 8. Error handling & failure semantics

- **Exit 2 (CONFIG):** emit a `.prom` carrying **only** `mesh_gate_config_error 1`, `mesh_gate_last_run_timestamp_seconds`, `mesh_gate_exec_duration_seconds`. Deliberately **omit** `verdict`/`adim`/`ext_port` — they are unknown, and fabricating a GREEN/RED would repeat the exact n8n 401-trap (a transient auth/reach failure that misclassified the gateway as down forever). The absent verdict + `config_error=1` is the unambiguous "sensor blind" signal.
- **Partial ADIM failure** (a single ssh subcommand times out): that ADIM = **fail** (fail-closed). Never silently treated as pass.
- **Atomic textfile write:** render to `mesh_gate.prom.tmp`, then `mv` into place — node_exporter never reads a half-written file.
- **Ledger:** `flock` around the append; a failed append must not crash the timer (logged, non-fatal).

## 9. Testing (TDD)

- **Unit — renderers:** given a synthetic internal state (`ADIM_*`, `PORT_OPEN`, verdict, warns), assert exact `text` / `json` / `prom` output against golden files.
- **Unit — classify:** mock `ss` / `ufw` / `iptables -L DOCKER-USER` / `docker inspect` / probe outputs (inject via stubbed `ssh` and `PROBE_TOOL`), assert verdict + per-ADIM booleans, including the fail-closed path (timeout → fail) and CONFIG path (exit 2 → config_error only).
- **Integration (deploy gate):** a read-only live dry-run against avm-02 producing a real verdict — analogous to the n8n verify-first gate; activation of the timer is blocked until this passes.
- **Meta-drill (manual, maintenance window):** stop `mesh-gate.timer` → confirm `MeshGateStale` fires. A monitor of the monitor.
- **Framework:** `bats` for shell units + golden fixtures, consistent with the `baseline-collector` house style (shell + structured output).

## 10. Deployment posture (design-only; apply = ops)

Code lands in the repo; deployment to avm-01 is an operator action, each step `--check`-gated (RED pre-apply, GREEN post-apply) per the VM-hardening house pattern:

1. Move `gate_check.sh` into `security/gate/` (+ `FORMAT` modes), version it.
2. Copy script + systemd units + rules to avm-01.
3. Point/confirm node_exporter textfile directory; drop `mesh_gate.prom` once.
4. Drop `mesh_gate.rules.yml` into Prometheus rules dir; reload Prometheus + Alertmanager.
5. Enable `mesh-gate.timer`.

**Rollback (clean, additive):** disable timer → remove textfile → remove rules → reload. No avm-02 state is ever touched.

**Secrets:** none new in the gateway path (ssh keys already on avm-01). The Slack `#security` webhook is provided by the operator and lives only in Alertmanager config on avm-01 — never in the repo, chat, or logs.

## 11. Prerequisites to verify at deploy (not blocking this spec)

- node_exporter on avm-01 launched with `--collector.textfile.directory=<dir>`; confirm the exact dir.
- ssh alias avm-01 → avm-02 over mesh (for internal ADIM); public IP reachable from avm-01 (for ext probe).
- `nc` (or `nmap`) present on avm-01.
- Prometheus rules dir + reload mechanism (SIGHUP / `/-/reload`) on avm-01.
- Slack `#security` incoming-webhook URL (operator-provided, secret).

## 12. Caveats

- **External vantage is off-host but may be provider-internal.** avm-01 → avm-02 *public IP* leaves avm-02's host, but if both VMs share a datacenter/ASN the path may not traverse the public internet. A true internet-side vantage (independent prober/ASN) is the **Max-external** follow-on, named below.
- **Fail-closed can produce RED on infrastructure flakiness** (e.g. a slow ssh). Acceptable: a security gate should err toward "prove it's safe," and `MeshGateConfigError` distinguishes "couldn't check" from "found a hole."

## 13. Out of scope (named follow-on tracks)

- Hash-chain on the JSONL ledger; ADB ship via `zmem` (V2 follow-on).
- Fleet / multi-host gate (all mesh nodes, not just avm-02).
- Per-role gate profiles (different port/bind policy per host class).
- Threat-model expansion beyond the 4 ADIM (e.g. TLS posture, package CVE drift).
- **P2.1 backstop *application*** (DOCKER-USER DROP rules) — design exists separately; apply is operator-gated.
- ChatOps summary via n8n (optional, read-only; reserved for digest, not alerting).
- **Max-external** independent internet-side prober.

## Links

- Sensor (current, unversioned): `/Users/z/infra/gate/gate_check.sh`
- Human audit narrative: `/Users/z/security-audit.md`
- Related spec (substrate-choice context): [`2026-06-25-n8n-mesh-deployment-design.md`](2026-06-25-n8n-mesh-deployment-design.md)
- avm-01 control plane: Prometheus + Grafana + NATS + node_exporter (10.10.0.1)
