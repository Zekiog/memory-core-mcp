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
#   TARGET_PUB_IP public IP for ext probe — REQUIRED, no default (security hardening)
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
# Security hardening: no IP default — caller must supply TARGET_PUB_IP explicitly.
# This prevents the VM's public IP from being embedded in source control.
TARGET_PUB_IP="${TARGET_PUB_IP:?TARGET_PUB_IP env var must be set (e.g. export TARGET_PUB_IP=x.x.x.x)}"
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
  # Project column 5 (Local Address:Port) locally so the Peer column (e.g. 0.0.0.0:*)
  # never reaches the public-bind grep below. Remote-side awk without {print $5} leaked
  # the peer column and caused false RED on mesh-bound sockets (regression task_726de0e9).
  out="$(ssh "$TARGET_HOST" "sudo ss -tulnp 2>/dev/null" 2>/dev/null \
    | awk '$5 ~ /:(5678|6379|5432)$/ {print $5}' || true)"
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
