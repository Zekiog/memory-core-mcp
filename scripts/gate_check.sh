#!/usr/bin/env bash
# gate_check.sh — P2.5-03/04 pre-action safety gate
# Usage: ./scripts/gate_check.sh <action_label>
# Exit 0 = safe to proceed | Exit 1 = ABORT
#
# Checks:
#   1. Disk >= 5 GB free
#   2. At least one runner worker running
#   3. n8n health 200
#   4. cloudflared active

set -euo pipefail

ACTION="${1:-unknown}"
PASS=0
FAIL=0
LOG_PREFIX="[gate_check | $ACTION]"

log()  { echo "$(date -u +%FT%TZ) $LOG_PREFIX $*"; }
pass() { log "PASS: $1"; PASS=$((PASS+1)); }
fail() { log "FAIL: $1"; FAIL=$((FAIL+1)); }

# 1. Disk free >= 5 GB
FREE_KB=$(df / | awk 'NR==2{print $4}')
FREE_GB=$(( FREE_KB / 1024 / 1024 ))
if [[ $FREE_GB -ge 5 ]]; then
    pass "disk free ${FREE_GB}GB >= 5GB"
else
    fail "disk free ${FREE_GB}GB < 5GB — action aborted"
fi

# 2. At least one runner process
RUNNER_COUNT=$(pgrep -c Runner.Worker 2>/dev/null || true)
if [[ $RUNNER_COUNT -gt 0 ]]; then
    pass "${RUNNER_COUNT} runner worker(s) running"
else
    fail "no runner workers found"
fi

# 3. n8n health
N8N_URL="${N8N_HEALTH_URL:-http://localhost:5678/healthz}"
HTTP=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 5 "$N8N_URL" || echo "000")
if [[ $HTTP == "200" ]]; then
    pass "n8n health $HTTP"
else
    fail "n8n health returned $HTTP"
fi

# 4. cloudflared
if pgrep -x cloudflared > /dev/null 2>&1; then
    pass "cloudflared process active"
else
    fail "cloudflared not running"
fi

# Summary
log "Result: ${PASS} passed, ${FAIL} failed"
if [[ $FAIL -gt 0 ]]; then
    log "GATE BLOCKED — action '$ACTION' will not execute"
    exit 1
fi
log "GATE OK — proceeding with '$ACTION'"
exit 0
