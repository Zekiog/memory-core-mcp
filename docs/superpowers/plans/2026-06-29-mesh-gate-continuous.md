# Mesh Gate: Continuous Assurance + Tamper-Aware Audit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the one-shot `gate_check.sh` (4-ADIM avm-02 public-exposure check) into a versioned, structured-output security sensor (`--format text|json|prom`) plus the scheduling, alerting, and append-only audit artifacts that make every verdict continuous and durable.

**Architecture:** The sensor stays read-only; rendering is split into a sourceable `lib/render.sh` so formatters are unit-testable in isolation. `gate_check.sh` populates structured per-ADIM globals, then dispatches to a renderer. systemd units, Prometheus alert rules, and a `flock`-guarded JSONL appender are produced as repo artifacts. Deployment to avm-01 is a documented operator runbook, **not** an automated task.

**Tech Stack:** Bash (kept 3.2-portable — no associative arrays), bats-core (tests with PATH-stubbed `ssh`/`nc` + golden fixtures), systemd timer/service, Prometheus exposition format, Alertmanager rule YAML, node_exporter textfile collector. Dev/test host: macOS (repo home) + CI; production runner: avm-01 (Linux).

**Spec:** `docs/superpowers/specs/2026-06-29-mesh-gate-continuous-design.md`

**Branch:** `spec/mesh-gate-continuous` (already created off `main`; spec already committed at `5dbde64`).

---

## Conventions used by every task

- Run all tests from the repo root: `bats security/gate/tests/`.
- Renderers consume **globals** (documented contract in `lib/render.sh`); tests set those globals directly, so no live target is needed.
- Determinism hooks (read by `gate_check.sh`, overridable in tests): `NOW_ISO`, `NOW_EPOCH`, `EXEC_DURATION`, `RUNNER`, `HOSTLABEL`.
- **`set -u` empty-array gotcha (bash 3.2/4.x):** expanding `"${ARR[@]}"` on an empty array under `set -u` raises "unbound variable". Always pass arrays with the guard idiom `${ARR[@]+"${ARR[@]}"}` and guard loops with `for x in ${ARR[@]+"${ARR[@]}"}; do`.

---

## Task 1: Version the baseline script + scaffold + bats harness

**Files:**
- Create dir: `security/gate/`, `security/gate/lib/`, `security/gate/audit/`, `security/gate/systemd/`, `security/gate/prometheus/`, `security/gate/tests/`, `security/gate/tests/fixtures/`
- Create: `security/gate/gate_check.sh` (copied verbatim from the current unversioned `/Users/z/infra/gate/gate_check.sh`)
- Create: `security/gate/tests/test_helper.bash`
- Create: `security/gate/tests/fixtures/ss_green.txt`, `ss_red.txt`, `ufw_ok.txt`, `dockeruser_empty.txt`, `docker_bind_ok.txt`, `docker_bind_wild.txt`
- Create: `security/gate/tests/smoke.bats`

- [ ] **Step 1: Ensure bats-core is available**

Run: `bats --version || brew install bats-core`
Expected: prints `Bats 1.x` (install if missing; macOS dev host).

- [ ] **Step 2: Create the directory tree**

```bash
cd /Users/z/src/memory-core-mcp
mkdir -p security/gate/lib security/gate/audit security/gate/systemd \
         security/gate/prometheus security/gate/tests/fixtures
```

- [ ] **Step 3: Copy the current sensor into the repo (version it)**

```bash
cp /Users/z/infra/gate/gate_check.sh security/gate/gate_check.sh
chmod +x security/gate/gate_check.sh
bash -n security/gate/gate_check.sh && echo "SYNTAX OK"
```
Expected: `SYNTAX OK`. (This is the pre-FORMAT baseline; later tasks refactor it.)

- [ ] **Step 4: Write fixtures**

`security/gate/tests/fixtures/ss_green.txt`:
```
Netid State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
tcp   LISTEN 0      511    10.10.0.2:5678     0.0.0.0:*         users:(("node",pid=101,fd=20))
```

`security/gate/tests/fixtures/ss_red.txt`:
```
Netid State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
tcp   LISTEN 0      511    0.0.0.0:5678       0.0.0.0:*         users:(("node",pid=101,fd=20))
```

`security/gate/tests/fixtures/ufw_ok.txt`:
```
Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip
```

`security/gate/tests/fixtures/dockeruser_empty.txt`:
```
Chain DOCKER-USER (1 references)
target     prot opt source               destination
```

`security/gate/tests/fixtures/docker_bind_ok.txt`:
```
/n8n {"5678/tcp":[{"HostIp":"10.10.0.2","HostPort":"5678"}]}
/redis null
/postgres null
```

`security/gate/tests/fixtures/docker_bind_wild.txt`:
```
/n8n {"5678/tcp":[{"HostIp":"","HostPort":"5678"}]}
/redis null
/postgres null
```

- [ ] **Step 5: Write the test helper (PATH stubs for `ssh` and `nc`)**

`security/gate/tests/test_helper.bash`:
```bash
# Shared bats helpers: build a stub bin dir on PATH so the gate never touches a real host.
GATE_DIR="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
FIXTURES="$BATS_TEST_DIRNAME/fixtures"

setup_stub_bin() {
  STUB_BIN="$(mktemp -d)"
  # --- stub ssh: route by the remote command string (last arg) ---
  cat >"$STUB_BIN/ssh" <<EOF
#!/usr/bin/env bash
cmd="\${@: -1}"
if [ "\${STUB_SSH_UNREACHABLE:-0}" = "1" ]; then exit 255; fi
case "\$cmd" in
  true) exit 0 ;;
  *"ss -tulnp"*)      cat "$FIXTURES/\${SS_FIXTURE:-ss_green.txt}" ;;
  *"ufw status"*)     cat "$FIXTURES/\${UFW_FIXTURE:-ufw_ok.txt}" ;;
  *"DOCKER-USER"*)    cat "$FIXTURES/\${DOCKERUSER_FIXTURE:-dockeruser_empty.txt}" ;;
  *"docker inspect"*) cat "$FIXTURES/\${DOCKERBIND_FIXTURE:-docker_bind_ok.txt}" ;;
  *) exit 0 ;;
esac
EOF
  # --- stub nc: control port 22 always open; gated ports open only if listed in STUB_OPEN_PORTS ---
  cat >"$STUB_BIN/nc" <<'EOF'
#!/usr/bin/env bash
port=""
for a in "$@"; do port="$a"; done   # last arg is the port
[ "$port" = "22" ] && exit 0
for p in ${STUB_OPEN_PORTS:-}; do [ "$p" = "$port" ] && exit 0; done
exit 1
EOF
  chmod +x "$STUB_BIN/ssh" "$STUB_BIN/nc"
  PATH="$STUB_BIN:$PATH"
}

teardown_stub_bin() { rm -rf "$STUB_BIN"; }
```

- [ ] **Step 6: Write the smoke test**

`security/gate/tests/smoke.bats`:
```bash
#!/usr/bin/env bats
load test_helper

@test "gate_check.sh passes bash -n" {
  run bash -n "$GATE_DIR/gate_check.sh"
  [ "$status" -eq 0 ]
}
```

- [ ] **Step 7: Run the smoke test**

Run: `bats security/gate/tests/smoke.bats`
Expected: `1 test, 0 failures`.

- [ ] **Step 8: Commit**

```bash
cd /Users/z/src/memory-core-mcp
git add security/gate
git commit -m "feat(gate): version baseline sensor + bats harness scaffold"
```

---

## Task 2: Renderers in `lib/render.sh` (text/json/prom/config) with golden tests

**Files:**
- Create: `security/gate/lib/render.sh`
- Create: `security/gate/tests/render.bats`
- Create golden fixtures: `security/gate/tests/fixtures/golden_text.txt`, `golden_json.txt`, `golden_prom.txt`, `golden_config_prom.txt`

- [ ] **Step 1: Write the golden fixtures (expected renderer output)**

`security/gate/tests/fixtures/golden_text.txt` (single line, trailing newline):
```
2026-06-29T12:00:00Z gate=GREEN target=79.76.57.223 probe=nc strict_p2_1=0 fails=none warns=docker-user:empty(P2.1-latent)
```

`security/gate/tests/fixtures/golden_json.txt`:
```
{"ts":"2026-06-29T12:00:00Z","host":"avm-02","verdict":"GREEN","exit":0,"fails":[],"warns":["docker-user:empty(P2.1-latent)"],"ext_ports":{"5678":"closed","6379":"closed","5432":"closed"},"probe":"nc","strict_p2_1":0,"runner":"avm-01","duration_s":2}
```

`security/gate/tests/fixtures/golden_prom.txt`:
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
# HELP mesh_gate_config_error Gate hit CONFIG error (exit 2)
# TYPE mesh_gate_config_error gauge
mesh_gate_config_error{host="avm-02"} 0
# HELP mesh_gate_exec_duration_seconds Wall time of the gate run
# TYPE mesh_gate_exec_duration_seconds gauge
mesh_gate_exec_duration_seconds{host="avm-02"} 2
# HELP mesh_gate_last_run_timestamp_seconds Unix time the gate completed
# TYPE mesh_gate_last_run_timestamp_seconds gauge
mesh_gate_last_run_timestamp_seconds{host="avm-02"} 1751193600
```

`security/gate/tests/fixtures/golden_config_prom.txt`:
```
# HELP mesh_gate_config_error Gate hit CONFIG error (exit 2)
# TYPE mesh_gate_config_error gauge
mesh_gate_config_error{host="avm-02"} 1
# HELP mesh_gate_exec_duration_seconds Wall time of the gate run
# TYPE mesh_gate_exec_duration_seconds gauge
mesh_gate_exec_duration_seconds{host="avm-02"} 0
# HELP mesh_gate_last_run_timestamp_seconds Unix time the gate completed
# TYPE mesh_gate_last_run_timestamp_seconds gauge
mesh_gate_last_run_timestamp_seconds{host="avm-02"} 1751193600
```

- [ ] **Step 2: Write the failing renderer tests**

`security/gate/tests/render.bats`:
```bash
#!/usr/bin/env bats
load test_helper

setup() {
  source "$GATE_DIR/lib/render.sh"
  # deterministic shared state for a GREEN run with a latent P2.1 warning
  NOW_ISO="2026-06-29T12:00:00Z"; NOW_EPOCH="1751193600"
  HOSTLABEL="avm-02"; RUNNER="avm-01"; TARGET_PUB_IP="79.76.57.223"
  PROBE_TOOL="nc"; STRICT_P2_1="0"; EXEC_DURATION="2"
  VERDICT="GREEN"; EXIT_CODE="0"; FORMAT="text"
  ADIM_SS="1"; ADIM_UFW="1"; ADIM_DOCKER="1"; ADIM_EXT="1"
  FAILS=(); WARNS=("docker-user:empty(P2.1-latent)")
  EXT_PORT_RESULTS=("5678:0" "6379:0" "5432:0")
  CONFIG_ERROR="0"; CONFIG_REASON=""
}

@test "render_text matches golden" {
  run render_text
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$FIXTURES/golden_text.txt")" ]
}

@test "render_json matches golden" {
  run render_json
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$FIXTURES/golden_json.txt")" ]
}

@test "render_prom matches golden" {
  run render_prom
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$FIXTURES/golden_prom.txt")" ]
}

@test "render_config_error prom matches golden" {
  FORMAT="prom"; CONFIG_ERROR="1"; EXEC_DURATION="0"
  run render_config_error
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$FIXTURES/golden_config_prom.txt")" ]
}

@test "json_escape escapes quote and backslash" {
  run json_escape 'a"b\c'
  [ "$output" = 'a\"b\\c' ]
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `bats security/gate/tests/render.bats`
Expected: FAIL — `render.sh` does not exist yet (`source` errors / functions undefined).

- [ ] **Step 4: Implement `lib/render.sh`**

`security/gate/lib/render.sh`:
```bash
#!/usr/bin/env bash
# render.sh — output formatters for gate_check.sh (sourced, not executed).
#
# Consumes these globals (set by gate_check.sh, or by a test before calling):
#   VERDICT GREEN|RED            EXIT_CODE 0|1
#   ADIM_SS ADIM_UFW ADIM_DOCKER ADIM_EXT   1=pass 0=fail
#   EXT_PORT_RESULTS[]  "port:open"  (open: 1=reachable=BAD, 0=closed=good)
#   STRICT_P2_1 0|1     PROBE_TOOL nmap|nc     CONFIG_ERROR 0|1   CONFIG_REASON str
#   EXEC_DURATION int   NOW_ISO iso8601   NOW_EPOCH unix   TARGET_PUB_IP str
#   HOSTLABEL str (metric host=)   RUNNER str   FORMAT text|json|prom
#   FAILS[] WARNS[]  token arrays
#   FORMAT is read only by render_config_error (to pick its own encoding).

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

# join args into a JSON array; empty -> []
json_array() {
  local first=1 out="[" a
  for a in "$@"; do
    if [ "$first" = 1 ]; then first=0; else out="$out,"; fi
    out="$out\"$(json_escape "$a")\""
  done
  printf '%s]' "$out"
}

render_text() {
  local fails_s warns_s
  fails_s="${FAILS[*]:-none}"
  warns_s="${WARNS[*]:-none}"
  printf '%s gate=%s target=%s probe=%s strict_p2_1=%s fails=%s warns=%s\n' \
    "$NOW_ISO" "$VERDICT" "$TARGET_PUB_IP" "$PROBE_TOOL" "$STRICT_P2_1" \
    "$fails_s" "$warns_s"
}

render_json() {
  local fails_json warns_json ext_json="{" first=1 pair port open state
  fails_json="$(json_array ${FAILS[@]+"${FAILS[@]}"})"
  warns_json="$(json_array ${WARNS[@]+"${WARNS[@]}"})"
  for pair in ${EXT_PORT_RESULTS[@]+"${EXT_PORT_RESULTS[@]}"}; do
    port="${pair%%:*}"; open="${pair##*:}"
    if [ "$open" = 1 ]; then state="open"; else state="closed"; fi
    if [ "$first" = 1 ]; then first=0; else ext_json="$ext_json,"; fi
    ext_json="$ext_json\"$port\":\"$state\""
  done
  ext_json="$ext_json}"
  printf '{"ts":"%s","host":"%s","verdict":"%s","exit":%s,"fails":%s,"warns":%s,"ext_ports":%s,"probe":"%s","strict_p2_1":%s,"runner":"%s","duration_s":%s}\n' \
    "$NOW_ISO" "$HOSTLABEL" "$VERDICT" "$EXIT_CODE" "$fails_json" "$warns_json" \
    "$ext_json" "$PROBE_TOOL" "$STRICT_P2_1" "$RUNNER" "$EXEC_DURATION"
}

render_prom() {
  local pair port open verdict_n
  verdict_n=0; [ "$VERDICT" = GREEN ] && verdict_n=1
  printf '# HELP mesh_gate_verdict Overall gate verdict (1=GREEN no exposure, 0=RED exposure found)\n'
  printf '# TYPE mesh_gate_verdict gauge\n'
  printf 'mesh_gate_verdict{host="%s"} %s\n' "$HOSTLABEL" "$verdict_n"
  printf '# HELP mesh_gate_adim Per-step result (1=pass, 0=fail)\n'
  printf '# TYPE mesh_gate_adim gauge\n'
  printf 'mesh_gate_adim{host="%s",adim="ss"} %s\n' "$HOSTLABEL" "$ADIM_SS"
  printf 'mesh_gate_adim{host="%s",adim="ufw"} %s\n' "$HOSTLABEL" "$ADIM_UFW"
  printf 'mesh_gate_adim{host="%s",adim="docker_bind"} %s\n' "$HOSTLABEL" "$ADIM_DOCKER"
  printf 'mesh_gate_adim{host="%s",adim="ext_probe"} %s\n' "$HOSTLABEL" "$ADIM_EXT"
  printf '# HELP mesh_gate_ext_port_open External reachability (1=OPEN=bad, 0=closed/filtered=good)\n'
  printf '# TYPE mesh_gate_ext_port_open gauge\n'
  for pair in ${EXT_PORT_RESULTS[@]+"${EXT_PORT_RESULTS[@]}"}; do
    port="${pair%%:*}"; open="${pair##*:}"
    printf 'mesh_gate_ext_port_open{host="%s",port="%s"} %s\n' "$HOSTLABEL" "$port" "$open"
  done
  printf '# HELP mesh_gate_strict_p2_1 STRICT_P2_1 in effect (1=empty DOCKER-USER counts as RED)\n'
  printf '# TYPE mesh_gate_strict_p2_1 gauge\n'
  printf 'mesh_gate_strict_p2_1{host="%s"} %s\n' "$HOSTLABEL" "$STRICT_P2_1"
  printf '# HELP mesh_gate_config_error Gate hit CONFIG error (exit 2)\n'
  printf '# TYPE mesh_gate_config_error gauge\n'
  printf 'mesh_gate_config_error{host="%s"} 0\n' "$HOSTLABEL"
  printf '# HELP mesh_gate_exec_duration_seconds Wall time of the gate run\n'
  printf '# TYPE mesh_gate_exec_duration_seconds gauge\n'
  printf 'mesh_gate_exec_duration_seconds{host="%s"} %s\n' "$HOSTLABEL" "$EXEC_DURATION"
  printf '# HELP mesh_gate_last_run_timestamp_seconds Unix time the gate completed\n'
  printf '# TYPE mesh_gate_last_run_timestamp_seconds gauge\n'
  printf 'mesh_gate_last_run_timestamp_seconds{host="%s"} %s\n' "$HOSTLABEL" "$NOW_EPOCH"
}

# exit-2 path: emit ONLY config_error=1 + duration + timestamp. verdict/adim/ext omitted (unknown).
render_config_error() {
  case "$FORMAT" in
    prom)
      printf '# HELP mesh_gate_config_error Gate hit CONFIG error (exit 2)\n'
      printf '# TYPE mesh_gate_config_error gauge\n'
      printf 'mesh_gate_config_error{host="%s"} 1\n' "$HOSTLABEL"
      printf '# HELP mesh_gate_exec_duration_seconds Wall time of the gate run\n'
      printf '# TYPE mesh_gate_exec_duration_seconds gauge\n'
      printf 'mesh_gate_exec_duration_seconds{host="%s"} %s\n' "$HOSTLABEL" "$EXEC_DURATION"
      printf '# HELP mesh_gate_last_run_timestamp_seconds Unix time the gate completed\n'
      printf '# TYPE mesh_gate_last_run_timestamp_seconds gauge\n'
      printf 'mesh_gate_last_run_timestamp_seconds{host="%s"} %s\n' "$HOSTLABEL" "$NOW_EPOCH"
      ;;
    json)
      printf '{"ts":"%s","host":"%s","verdict":"CONFIG","exit":2,"config_error":1,"reason":"%s","runner":"%s","duration_s":%s}\n' \
        "$NOW_ISO" "$HOSTLABEL" "$(json_escape "$CONFIG_REASON")" "$RUNNER" "$EXEC_DURATION"
      ;;
    *)
      printf '%s gate=CONFIG target=%s reason=%s\n' "$NOW_ISO" "$TARGET_PUB_IP" "$CONFIG_REASON" >&2
      ;;
  esac
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `bats security/gate/tests/render.bats`
Expected: `5 tests, 0 failures`.

- [ ] **Step 6: Commit**

```bash
cd /Users/z/src/memory-core-mcp
git add security/gate/lib/render.sh security/gate/tests/render.bats security/gate/tests/fixtures
git commit -m "feat(gate): add text/json/prom/config renderers with golden tests"
```

---

## Task 3: Refactor `gate_check.sh` — structured state + FORMAT dispatch

**Files:**
- Modify (full rewrite): `security/gate/gate_check.sh`
- Create: `security/gate/tests/classify.bats`

- [ ] **Step 1: Write the failing classify tests**

`security/gate/tests/classify.bats`:
```bash
#!/usr/bin/env bats
load test_helper

setup() { setup_stub_bin; }
teardown() { teardown_stub_bin; }

run_gate() {
  NOW_ISO="2026-06-29T12:00:00Z" NOW_EPOCH="1751193600" EXEC_DURATION_OVERRIDE="2" \
  RUNNER="avm-01" HOSTLABEL="avm-02" PROBE_TOOL="nc" \
  TARGET_HOST="z-agentic-vm-02" TARGET_PUB_IP="79.76.57.223" \
  FORMAT="$1" bash "$GATE_DIR/gate_check.sh"
}

@test "all-clean run is GREEN, exit 0, prom verdict=1" {
  run run_gate prom
  [ "$status" -eq 0 ]
  echo "$output" | grep -q 'mesh_gate_verdict{host="avm-02"} 1'
}

@test "public bind on ss makes verdict RED, exit 1, adim ss=0" {
  export SS_FIXTURE="ss_red.txt"   # export so the stubbed ssh subprocess sees it
  run run_gate prom
  [ "$status" -eq 1 ]
  echo "$output" | grep -q 'mesh_gate_verdict{host="avm-02"} 0'
  echo "$output" | grep -q 'mesh_gate_adim{host="avm-02",adim="ss"} 0'
}

@test "externally open gated port makes verdict RED and ext_port_open=1" {
  export STUB_OPEN_PORTS="5678"   # export so the stubbed nc subprocess sees it
  run run_gate prom
  [ "$status" -eq 1 ]
  echo "$output" | grep -q 'mesh_gate_ext_port_open{host="avm-02",port="5678"} 1'
  echo "$output" | grep -q 'mesh_gate_adim{host="avm-02",adim="ext_probe"} 0'
}

@test "json format emits a single parseable verdict line" {
  run run_gate json
  [ "$status" -eq 0 ]
  echo "$output" | grep -q '"verdict":"GREEN"'
  echo "$output" | grep -q '"ext_ports":{"5678":"closed","6379":"closed","5432":"closed"}'
}

@test "text format default is backward-compatible single line" {
  run run_gate text
  [ "$status" -eq 0 ]
  echo "$output" | grep -q 'gate=GREEN target=79.76.57.223 probe=nc'
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bats security/gate/tests/classify.bats`
Expected: FAIL — current `gate_check.sh` has no `FORMAT` handling and emits only the text line / exits via old `main`.

- [ ] **Step 3: Rewrite `security/gate/gate_check.sh`**

`security/gate/gate_check.sh`:
```bash
#!/usr/bin/env bash
# gate_check.sh — AVM-02 Public Exposure Gate (read-only, idempotent)
#
# 4 ADIM verification:
#   1) ss      — internal listener bind (mesh/loopback OK; 0.0.0.0/:: NOT OK)
#   2) ufw     — default-deny + DOCKER-USER backstop presence (P2.1)
#   3) docker  — port-binding HostIp must be mesh-IP or loopback (no wildcard)
#   4) ext     — external TCP reachability — MUST be filtered/closed
#
# Exit codes: 0 GREEN | 1 RED | 2 CONFIG (target/tool unreachable).
#
# Env overrides:
#   TARGET_HOST  ssh alias (default z-agentic-vm-02)
#   TARGET_PUB_IP public IP for ext probe (default 79.76.57.223)
#   PORTS        space-separated TCP list (default "5678 6379 5432")
#   STRICT_P2_1  "1" -> empty DOCKER-USER = RED (default 0 -> WARN)
#   PROBE_TOOL   nmap|nc|auto (default auto, prefer nmap)
#   FORMAT       text|json|prom (default text)
#   HOSTLABEL    metric host= label (default avm-02)
#   RUNNER       audit runner label (default `hostname -s`)
#   NOW_ISO NOW_EPOCH EXEC_DURATION_OVERRIDE   — test determinism hooks
#
# State: none mutated. Secrets: none read. Safe to cron/CI.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/render.sh
. "$HERE/lib/render.sh"

TARGET_HOST="${TARGET_HOST:-z-agentic-vm-02}"
TARGET_PUB_IP="${TARGET_PUB_IP:-79.76.57.223}"
PORTS="${PORTS:-5678 6379 5432}"
STRICT_P2_1="${STRICT_P2_1:-0}"
PROBE_TOOL="${PROBE_TOOL:-auto}"
FORMAT="${FORMAT:-text}"
HOSTLABEL="${HOSTLABEL:-avm-02}"
RUNNER="${RUNNER:-$(hostname -s 2>/dev/null || echo unknown)}"
NOW_ISO="${NOW_ISO:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
NOW_EPOCH="${NOW_EPOCH:-$(date +%s)}"

EXEC_START="$(date +%s)"
VERDICT=GREEN
EXIT_CODE=0
CONFIG_ERROR=0
CONFIG_REASON=""
FAILS=()
WARNS=()
EXT_PORT_RESULTS=()
ADIM_SS=1; ADIM_UFW=1; ADIM_DOCKER=1; ADIM_EXT=1

preflight() {
  if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$TARGET_HOST" true 2>/dev/null; then
    CONFIG_ERROR=1; CONFIG_REASON="ssh:unreachable:$TARGET_HOST"; return 1
  fi
  if [ "$PROBE_TOOL" = "auto" ]; then
    if command -v nmap >/dev/null 2>&1; then PROBE_TOOL=nmap; else PROBE_TOOL=nc; fi
  fi
  case "$PROBE_TOOL" in
    nmap) command -v nmap >/dev/null 2>&1 || { CONFIG_ERROR=1; CONFIG_REASON="nmap:missing"; return 1; } ;;
    nc)   command -v nc   >/dev/null 2>&1 || { CONFIG_ERROR=1; CONFIG_REASON="nc:missing";   return 1; } ;;
    *)    CONFIG_ERROR=1; CONFIG_REASON="PROBE_TOOL:invalid:$PROBE_TOOL"; return 1 ;;
  esac
  return 0
}

step1_ss() {
  local out
  out="$(ssh "$TARGET_HOST" \
    "sudo ss -tulnp 2>/dev/null | awk '\$5 ~ /:(5678|6379|5432)\$/'" 2>/dev/null || true)"
  if printf '%s\n' "$out" | grep -Eq '(^|[[:space:]])0\.0\.0\.0:|(^|[[:space:]])\[::\]?:|(^|[[:space:]]):::'; then
    FAILS+=("ss:public-bind"); ADIM_SS=0
  fi
}

step2_ufw_dockeruser() {
  local ufw_out du_out
  ufw_out="$(ssh "$TARGET_HOST" "sudo ufw status verbose 2>/dev/null" 2>/dev/null || true)"
  if ! printf '%s\n' "$ufw_out" | grep -q "Default: deny (incoming)"; then
    FAILS+=("ufw:not-default-deny"); ADIM_UFW=0
  fi
  du_out="$(ssh "$TARGET_HOST" "sudo iptables -L DOCKER-USER -n 2>/dev/null" 2>/dev/null || true)"
  if ! printf '%s\n' "$du_out" | grep -Eq '^[[:space:]]*(DROP|REJECT|RETURN)'; then
    if [ "$STRICT_P2_1" = "1" ]; then
      FAILS+=("docker-user:empty(P2.1-gap)"); ADIM_UFW=0
    else
      WARNS+=("docker-user:empty(P2.1-latent)")
    fi
  fi
}

step3_docker_bind() {
  local out
  out="$(ssh "$TARGET_HOST" \
    'docker ps -q | xargs -r docker inspect --format "{{.Name}} {{json .HostConfig.PortBindings}}"' \
    2>/dev/null || true)"
  if printf '%s\n' "$out" | grep -Eq '"HostIp":""'; then
    FAILS+=("docker:wildcard-bind"); ADIM_DOCKER=0
  fi
}

step4_external_probe() {
  local p
  for p in $PORTS; do
    if probe_port "$TARGET_PUB_IP" "$p"; then
      EXT_PORT_RESULTS+=("$p:1"); FAILS+=("ext:open:$p"); ADIM_EXT=0
    else
      EXT_PORT_RESULTS+=("$p:0")
    fi
  done
  # control: port 22 SHOULD be reachable; if not, the probe path itself is broken
  if ! probe_port "$TARGET_PUB_IP" 22; then
    FAILS+=("ext:probe-broken"); ADIM_EXT=0
  fi
}

probe_port() {
  local ip="$1" port="$2"
  case "$PROBE_TOOL" in
    nmap) nmap -Pn -p "$port" --host-timeout 5s -oG - "$ip" 2>/dev/null | grep -q "${port}/open/" ;;
    nc)   nc -z -w 4 "$ip" "$port" >/dev/null 2>&1 ;;
  esac
}

render_dispatch() {
  case "$FORMAT" in
    json) render_json ;;
    prom) render_prom ;;
    *)    render_text ;;
  esac
}

main() {
  if ! preflight; then
    EXEC_DURATION="${EXEC_DURATION_OVERRIDE:-$(( $(date +%s) - EXEC_START ))}"
    render_config_error
    exit 2
  fi
  step1_ss
  step2_ufw_dockeruser
  step3_docker_bind
  step4_external_probe

  [ ${#FAILS[@]} -gt 0 ] && VERDICT=RED
  [ "$VERDICT" = GREEN ] && EXIT_CODE=0 || EXIT_CODE=1
  EXEC_DURATION="${EXEC_DURATION_OVERRIDE:-$(( $(date +%s) - EXEC_START ))}"

  render_dispatch
  exit "$EXIT_CODE"
}

main "$@"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bats security/gate/tests/classify.bats`
Expected: `5 tests, 0 failures`.

- [ ] **Step 5: Re-run the full suite (no regressions)**

Run: `bats security/gate/tests/`
Expected: all tests pass (smoke + render + classify).

- [ ] **Step 6: Commit**

```bash
cd /Users/z/src/memory-core-mcp
git add security/gate/gate_check.sh security/gate/tests/classify.bats
git commit -m "feat(gate): structured per-ADIM state + FORMAT dispatch (text/json/prom)"
```

---

## Task 4: CONFIG-error path end-to-end test

**Files:**
- Create: `security/gate/tests/config_error.bats`

- [ ] **Step 1: Write the failing test**

`security/gate/tests/config_error.bats`:
```bash
#!/usr/bin/env bats
load test_helper

setup() { setup_stub_bin; }
teardown() { teardown_stub_bin; }

@test "unreachable target yields exit 2 and config_error=1, no verdict line (prom)" {
  export STUB_SSH_UNREACHABLE=1 NOW_ISO="2026-06-29T12:00:00Z" NOW_EPOCH="1751193600"
  export EXEC_DURATION_OVERRIDE="0" HOSTLABEL="avm-02" PROBE_TOOL="nc" FORMAT="prom"
  run bash "$GATE_DIR/gate_check.sh"
  [ "$status" -eq 2 ]
  echo "$output" | grep -q 'mesh_gate_config_error{host="avm-02"} 1'
  ! echo "$output" | grep -q 'mesh_gate_verdict'
}

@test "unreachable target yields CONFIG json with reason" {
  export STUB_SSH_UNREACHABLE=1 NOW_ISO="2026-06-29T12:00:00Z" NOW_EPOCH="1751193600"
  export EXEC_DURATION_OVERRIDE="0" HOSTLABEL="avm-02" RUNNER="avm-01" PROBE_TOOL="nc" FORMAT="json"
  run bash "$GATE_DIR/gate_check.sh"
  [ "$status" -eq 2 ]
  echo "$output" | grep -q '"verdict":"CONFIG"'
  echo "$output" | grep -q '"reason":"ssh:unreachable:z-agentic-vm-02"'
}
```

- [ ] **Step 2: Run test to verify it fails, then passes**

Run: `bats security/gate/tests/config_error.bats`
Expected: PASS (the CONFIG path was implemented in Task 3; this test locks the behavior). If it FAILS, the regression is in `main`/`preflight` — fix `gate_check.sh`, not the test.

- [ ] **Step 3: Commit**

```bash
cd /Users/z/src/memory-core-mcp
git add security/gate/tests/config_error.bats
git commit -m "test(gate): lock CONFIG-error path (exit 2, config_error=1, no fabricated verdict)"
```

---

## Task 5: `audit/append_jsonl.sh` — flock-guarded append

**Files:**
- Create: `security/gate/audit/append_jsonl.sh`
- Create: `security/gate/tests/append.bats`

- [ ] **Step 1: Write the failing test**

`security/gate/tests/append.bats`:
```bash
#!/usr/bin/env bats
load test_helper

@test "append_jsonl appends each stdin line, preserving order" {
  tmp="$(mktemp -d)"; ledger="$tmp/security-audit.jsonl"
  printf '{"n":1}\n' | bash "$GATE_DIR/audit/append_jsonl.sh" "$ledger"
  printf '{"n":2}\n' | bash "$GATE_DIR/audit/append_jsonl.sh" "$ledger"
  [ "$(wc -l < "$ledger" | tr -d ' ')" = "2" ]
  [ "$(sed -n 1p "$ledger")" = '{"n":1}' ]
  [ "$(sed -n 2p "$ledger")" = '{"n":2}' ]
  rm -rf "$tmp"
}

@test "append_jsonl creates the ledger and parent dir if missing" {
  tmp="$(mktemp -d)"; ledger="$tmp/sub/dir/security-audit.jsonl"
  printf '{"n":1}\n' | bash "$GATE_DIR/audit/append_jsonl.sh" "$ledger"
  [ -f "$ledger" ]
  rm -rf "$tmp"
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats security/gate/tests/append.bats`
Expected: FAIL — `append_jsonl.sh` does not exist.

- [ ] **Step 3: Implement `audit/append_jsonl.sh`**

`security/gate/audit/append_jsonl.sh`:
```bash
#!/usr/bin/env bash
# append_jsonl.sh — append one JSON line (from stdin) to an append-only ledger.
# Uses flock when available (Linux/avm-01); falls back to a plain append on hosts
# without flock (macOS dev) — safe because the 15m serial cadence has no contention.
#
# Usage: gate_check.sh --format json | append_jsonl.sh /var/lib/mesh-gate/security-audit.jsonl
set -uo pipefail

LEDGER="${1:?usage: append_jsonl.sh <ledger-path>}"
mkdir -p "$(dirname "$LEDGER")"

line="$(cat)"
[ -n "$line" ] || { echo "append_jsonl: empty stdin, nothing appended" >&2; exit 0; }

if command -v flock >/dev/null 2>&1; then
  exec 9>>"$LEDGER"
  flock 9
  printf '%s\n' "$line" >&9
  flock -u 9
  exec 9>&-
else
  printf '%s\n' "$line" >>"$LEDGER"
fi
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats security/gate/tests/append.bats`
Expected: `2 tests, 0 failures`.

- [ ] **Step 5: Commit**

```bash
cd /Users/z/src/memory-core-mcp
git add security/gate/audit/append_jsonl.sh security/gate/tests/append.bats
git commit -m "feat(gate): flock-guarded JSONL audit appender"
```

---

## Task 6: systemd units (service + timer)

**Files:**
- Create: `security/gate/systemd/mesh-gate.service`
- Create: `security/gate/systemd/mesh-gate.timer`

These are config, not code — validate with a directive check (portable) and `systemd-analyze verify` at deploy (Linux only).

- [ ] **Step 1: Write `mesh-gate.service`**

`security/gate/systemd/mesh-gate.service`:
```ini
[Unit]
Description=Mesh Gate — avm-02 public-exposure continuous assurance
Documentation=https://internal/docs/superpowers/specs/2026-06-29-mesh-gate-continuous-design.md
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# Adjust paths at deploy. TEXTFILE_DIR must match node_exporter --collector.textfile.directory.
Environment=GATE_HOME=/opt/mesh-gate
Environment=TEXTFILE_DIR=/var/lib/node_exporter/textfile_collector
Environment=LEDGER=/var/lib/mesh-gate/security-audit.jsonl
Environment=HOSTLABEL=avm-02
Environment=RUNNER=avm-01
# 1) prom -> atomic textfile;  2) json -> append ledger
ExecStart=/bin/bash -c '"$GATE_HOME/gate_check.sh" --as FORMAT=prom > "$TEXTFILE_DIR/mesh_gate.prom.tmp" 2>/dev/null; mv "$TEXTFILE_DIR/mesh_gate.prom.tmp" "$TEXTFILE_DIR/mesh_gate.prom"'
ExecStart=/bin/bash -c 'FORMAT=json "$GATE_HOME/gate_check.sh" | "$GATE_HOME/audit/append_jsonl.sh" "$LEDGER"'
# Gate exit 1 (RED) / 2 (CONFIG) are expected, non-fatal to the unit:
SuccessExitStatus=0 1 2
Nice=10

[Install]
WantedBy=multi-user.target
```

> Note: `gate_check.sh` reads `FORMAT` from the environment (the first `ExecStart` sets it inline via the wrapper `FORMAT=prom`; correct the wrapper to `FORMAT=prom "$GATE_HOME/gate_check.sh"` at deploy if the `--as` shorthand is not wired). The script's public contract is the `FORMAT` env var.

- [ ] **Step 2: Fix the first ExecStart to use the FORMAT env var (the script's real contract)**

Replace the first `ExecStart` line with:
```ini
ExecStart=/bin/bash -c 'FORMAT=prom "$GATE_HOME/gate_check.sh" > "$TEXTFILE_DIR/mesh_gate.prom.tmp" 2>/dev/null && mv "$TEXTFILE_DIR/mesh_gate.prom.tmp" "$TEXTFILE_DIR/mesh_gate.prom"'
```
Rationale: `gate_check.sh` consumes `FORMAT` from the environment; there is no `--as` flag. Keep the contract to one mechanism (env var).

- [ ] **Step 3: Write `mesh-gate.timer`**

`security/gate/systemd/mesh-gate.timer`:
```ini
[Unit]
Description=Run Mesh Gate every 15 minutes
Documentation=https://internal/docs/superpowers/specs/2026-06-29-mesh-gate-continuous-design.md

[Timer]
OnBootSec=2min
OnCalendar=*:0/15
RandomizedDelaySec=60
Persistent=true
Unit=mesh-gate.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Portable directive sanity check**

Run:
```bash
grep -q '^Type=oneshot' security/gate/systemd/mesh-gate.service && \
grep -q '^OnCalendar=\*:0/15' security/gate/systemd/mesh-gate.timer && \
grep -q '^SuccessExitStatus=0 1 2' security/gate/systemd/mesh-gate.service && \
echo "UNIT DIRECTIVES OK"
```
Expected: `UNIT DIRECTIVES OK`.
(At deploy on avm-01, additionally run `systemd-analyze verify security/gate/systemd/mesh-gate.{service,timer}` — Linux only.)

- [ ] **Step 5: Commit**

```bash
cd /Users/z/src/memory-core-mcp
git add security/gate/systemd
git commit -m "feat(gate): systemd oneshot service + 15m timer (atomic prom write + ledger append)"
```

---

## Task 7: Prometheus alert rules

**Files:**
- Create: `security/gate/prometheus/mesh_gate.rules.yml`

- [ ] **Step 1: Write the rules file**

`security/gate/prometheus/mesh_gate.rules.yml`:
```yaml
groups:
  - name: mesh_gate
    rules:
      - alert: MeshGateRED
        expr: mesh_gate_verdict == 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Mesh gate RED on {{ $labels.host }} — public exposure detected"
          description: "gate_check.sh verdict=RED. Inspect mesh_gate_adim / mesh_gate_ext_port_open for {{ $labels.host }}."
      - alert: MeshGateStale
        expr: time() - mesh_gate_last_run_timestamp_seconds > 3600
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Mesh gate sensor STALE on {{ $labels.host }} — no fresh run >1h (timer dead?)"
          description: "The security sensor stopped producing verdicts. A silent monitor is worse than none."
      - alert: MeshGateConfigError
        expr: mesh_gate_config_error == 1
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "Mesh gate CONFIG error on {{ $labels.host }} — ssh/probe tool broken, gate blind"
          description: "Gate hit exit 2 for >15m. The gate cannot verify avm-02; treat as unknown, not safe."
```

- [ ] **Step 2: Validate YAML (portable) — and note the authoritative check**

Run:
```bash
python3 -c "import yaml,sys; d=yaml.safe_load(open('security/gate/prometheus/mesh_gate.rules.yml')); assert d['groups'][0]['name']=='mesh_gate'; assert len(d['groups'][0]['rules'])==3; print('RULES YAML OK')"
```
Expected: `RULES YAML OK`.
(At deploy on avm-01: `promtool check rules security/gate/prometheus/mesh_gate.rules.yml` is the authoritative validation.)

- [ ] **Step 3: Commit**

```bash
cd /Users/z/src/memory-core-mcp
git add security/gate/prometheus/mesh_gate.rules.yml
git commit -m "feat(gate): Prometheus alert rules (RED / Stale / ConfigError three-way)"
```

---

## Task 8: README operator runbook + final self-review

**Files:**
- Create: `security/gate/README.md`

- [ ] **Step 1: Write the runbook**

`security/gate/README.md`:
```markdown
# Mesh Gate — avm-02 continuous public-exposure assurance

Read-only security sensor (`gate_check.sh`, 4 ADIM) + scheduling + alerting + audit.
Design: `../../docs/superpowers/specs/2026-06-29-mesh-gate-continuous-design.md`.

## What it does
- ADIM 1 `ss` internal bind · 2 `ufw`+DOCKER-USER · 3 `docker` host-bind · 4 external probe.
- Verdict GREEN(0)/RED(1)/CONFIG(2). `FORMAT=text|json|prom`.
- avm-01 runs it every 15m: prom -> node_exporter textfile; json -> append-only ledger.
- Alerts: MeshGateRED (verdict=0), MeshGateStale (sensor dead >1h), MeshGateConfigError (gate blind >15m).

## Local test
    bats security/gate/tests/

## Deploy (operator-gated — touches avm-01 only; NEVER mutates avm-02)
1. Copy `security/gate/` to avm-01 `/opt/mesh-gate/` (keep `lib/` next to `gate_check.sh`).
2. Confirm node_exporter runs with `--collector.textfile.directory=<DIR>`; set `TEXTFILE_DIR` in the unit to `<DIR>`.
3. Smoke (read-only): `FORMAT=text /opt/mesh-gate/gate_check.sh` → expect a `gate=GREEN ...` line, exit 0.
4. Install units: copy `systemd/mesh-gate.{service,timer}` to `/etc/systemd/system/`, `systemctl daemon-reload`.
5. `systemd-analyze verify mesh-gate.service mesh-gate.timer`.
6. Drop `prometheus/mesh_gate.rules.yml` into the Prometheus rules dir; `promtool check rules` it; reload Prometheus.
7. Add a Slack `#security` receiver to Alertmanager (webhook provided out-of-band, never committed); reload Alertmanager.
8. `systemctl enable --now mesh-gate.timer`. Confirm first run: `systemctl start mesh-gate.service && cat <DIR>/mesh_gate.prom`.

## Rollback (clean, additive — no avm-02 state ever touched)
    systemctl disable --now mesh-gate.timer
    rm -f <DIR>/mesh_gate.prom <DIR>/mesh_gate.prom.tmp
    rm -f /etc/systemd/system/mesh-gate.service /etc/systemd/system/mesh-gate.timer
    systemctl daemon-reload
    # remove mesh_gate.rules.yml from the Prometheus rules dir; reload Prometheus

## Follow-on (not in this cycle)
- Hash-chain the ledger (prev_sha256/sha256); ship verdicts to ADB via `zmem add` (scope security-gate).
- Wire `bats security/gate/tests/` into repo CI.
- Max-external: independent internet-side prober (true off-ASN vantage).
- P2.1 backstop application (DOCKER-USER DROP rules) — separate operator-gated change.
```

- [ ] **Step 2: Run the full suite one last time**

Run: `bats security/gate/tests/`
Expected: all tests pass (smoke 1 + render 5 + classify 5 + config 2 + append 2 = 15).

- [ ] **Step 3: Commit**

```bash
cd /Users/z/src/memory-core-mcp
git add security/gate/README.md
git commit -m "docs(gate): operator deploy/rollback runbook + follow-on notes"
```

- [ ] **Step 4: Final self-review against the spec**

Confirm each spec section maps to a delivered artifact:
- §4 FORMAT modes → Task 2/3. §5 metric schema → `render_prom` golden (Task 2). §6 alert rules → Task 7.
- §7 JSONL ledger → `render_json` + `append_jsonl.sh` (Task 2/5). §8 error semantics → Task 3/4 (fail-closed via `ADIM_*=0`; CONFIG omits verdict).
- §9 testing → bats suite. §10 deploy posture → README. §3 components → all files present.
Run: `ls -R security/gate` and confirm the tree matches spec §3.

---

## Plan Self-Review

**Spec coverage:** Every spec section (§3 components, §4 FORMAT, §5 metrics, §6 alerts, §7 ledger, §8 errors, §9 tests, §10 deploy) maps to a task above. Follow-ons (§13: hash-chain, ADB ship, fleet, Max-external, P2.1 apply) are explicitly deferred in the README — not implemented, by design.

**Placeholder scan:** Deploy-time unknowns (`<DIR>` node_exporter textfile dir, Slack webhook) are operator inputs flagged in the README/spec §11, not lazy placeholders. All code steps contain complete, runnable content.

**Type/contract consistency:** Global names are identical across `lib/render.sh`, `gate_check.sh`, and every test (`VERDICT`, `ADIM_SS/UFW/DOCKER/EXT`, `EXT_PORT_RESULTS` as `"port:open"`, `EXIT_CODE`, `CONFIG_ERROR/REASON`, `NOW_ISO/NOW_EPOCH`, `EXEC_DURATION`/`EXEC_DURATION_OVERRIDE`, `HOSTLABEL`, `RUNNER`, `FORMAT`). The sensor's public contract is the `FORMAT` env var (no flag); the systemd unit uses it consistently after Task 6 Step 2.
