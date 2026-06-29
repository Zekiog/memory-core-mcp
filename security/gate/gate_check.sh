#!/usr/bin/env bash
# gate_check.sh — AVM-02 Public Exposure Gate (read-only, idempotent)
#
# 4 ADIM verification:
#   1) ss      — internal listener bind (mesh/loopback OK; 0.0.0.0/:: NOT OK)
#   2) ufw     — default-deny + DOCKER-USER backstop presence
#   3) docker  — port-binding HostIp must be mesh-IP or loopback (no wildcard)
#   4) ext     — external TCP reachability — MUST be filtered/closed
#
# Exit codes:
#   0  GREEN   — all 4 ADIM clean (or only documented latent warnings in non-strict)
#   1  RED     — at least one ADIM failed
#   2  CONFIG  — script preconditions not met (target unreachable, missing tools)
#
# Env overrides:
#   TARGET_HOST      ssh alias                (default: z-agentic-vm-02)
#   TARGET_PUB_IP    public IP for ext probe  (default: 79.76.57.223)
#   PORTS            space-separated TCP list (default: "5678 6379 5432")
#   STRICT_P2_1      "1" → empty DOCKER-USER  = RED (default: 0 → WARN)
#   PROBE_TOOL       "nmap" | "nc" | "auto"   (default: auto, prefer nmap)
#
# State: none mutated. Secrets: none read. Safe to cron/CI.

set -uo pipefail

TARGET_HOST="${TARGET_HOST:-z-agentic-vm-02}"
TARGET_PUB_IP="${TARGET_PUB_IP:-79.76.57.223}"
PORTS="${PORTS:-5678 6379 5432}"
STRICT_P2_1="${STRICT_P2_1:-0}"
PROBE_TOOL="${PROBE_TOOL:-auto}"

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
FAILS=()
WARNS=()
VERDICT=GREEN

# ---------- preconditions ----------
preflight() {
  if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$TARGET_HOST" true 2>/dev/null; then
    printf '%s gate=CONFIG target=%s fails=ssh:unreachable:%s\n' \
      "$TS" "$TARGET_PUB_IP" "$TARGET_HOST" >&2
    exit 2
  fi
  if [ "$PROBE_TOOL" = "auto" ]; then
    if command -v nmap >/dev/null 2>&1; then PROBE_TOOL=nmap; else PROBE_TOOL=nc; fi
  fi
  case "$PROBE_TOOL" in
    nmap) command -v nmap >/dev/null 2>&1 || { echo "CONFIG: nmap missing" >&2; exit 2; } ;;
    nc)   command -v nc   >/dev/null 2>&1 || { echo "CONFIG: nc missing"   >&2; exit 2; } ;;
    *)    echo "CONFIG: PROBE_TOOL must be nmap|nc|auto" >&2; exit 2 ;;
  esac
}

# ---------- ADIM 1 — ss (internal listener bind) ----------
step1_ss() {
  local out
  out="$(ssh "$TARGET_HOST" \
    "sudo ss -tulnp 2>/dev/null | awk '\$5 ~ /:(5678|6379|5432)\$/'" 2>/dev/null || true)"
  if printf '%s\n' "$out" | grep -Eq '(^|[[:space:]])0\.0\.0\.0:|(^|[[:space:]])\[::\]?:|(^|[[:space:]]):::'; then
    FAILS+=("ss:public-bind")
  fi
}

# ---------- ADIM 2 — UFW default-deny + DOCKER-USER backstop ----------
step2_ufw_dockeruser() {
  local ufw_out du_out
  ufw_out="$(ssh "$TARGET_HOST" "sudo ufw status verbose 2>/dev/null" 2>/dev/null || true)"
  printf '%s\n' "$ufw_out" | grep -q "Default: deny (incoming)" \
    || FAILS+=("ufw:not-default-deny")

  du_out="$(ssh "$TARGET_HOST" "sudo iptables -L DOCKER-USER -n 2>/dev/null" 2>/dev/null || true)"
  if ! printf '%s\n' "$du_out" | grep -Eq '^[[:space:]]*(DROP|REJECT|RETURN)'; then
    if [ "$STRICT_P2_1" = "1" ]; then
      FAILS+=("docker-user:empty(P2.1-gap)")
    else
      WARNS+=("docker-user:empty(P2.1-latent)")
    fi
  fi
}

# ---------- ADIM 3 — Docker host-bind ----------
step3_docker_bind() {
  local out
  out="$(ssh "$TARGET_HOST" \
    'docker ps -q | xargs -r docker inspect --format "{{.Name}} {{json .HostConfig.PortBindings}}"' \
    2>/dev/null || true)"
  if printf '%s\n' "$out" | grep -Eq '"HostIp":""'; then
    FAILS+=("docker:wildcard-bind")
  fi
}

# ---------- ADIM 4 — external TCP probe ----------
step4_external_probe() {
  local p
  for p in $PORTS; do
    if probe_port "$TARGET_PUB_IP" "$p"; then
      FAILS+=("ext:open:$p")
    fi
  done
  # control: port 22 SHOULD be reachable; if not, the probe path itself is broken
  probe_port "$TARGET_PUB_IP" 22 || FAILS+=("ext:probe-broken")
}

probe_port() {
  local ip="$1" port="$2"
  case "$PROBE_TOOL" in
    nmap)
      nmap -Pn -p "$port" --host-timeout 5s -oG - "$ip" 2>/dev/null \
        | grep -q "${port}/open/"
      ;;
    nc)
      nc -z -w 4 "$ip" "$port" >/dev/null 2>&1
      ;;
  esac
}

# ---------- run ----------
main() {
  preflight
  step1_ss
  step2_ufw_dockeruser
  step3_docker_bind
  step4_external_probe

  [ ${#FAILS[@]} -gt 0 ] && VERDICT=RED

  local fails_s warns_s
  fails_s="${FAILS[*]:-none}"
  warns_s="${WARNS[*]:-none}"
  printf '%s gate=%s target=%s probe=%s strict_p2_1=%s fails=%s warns=%s\n' \
    "$TS" "$VERDICT" "$TARGET_PUB_IP" "$PROBE_TOOL" "$STRICT_P2_1" \
    "$fails_s" "$warns_s"

  [ "$VERDICT" = GREEN ] && exit 0 || exit 1
}

main "$@"
