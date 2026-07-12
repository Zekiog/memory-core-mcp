#!/usr/bin/env bash
# mesh-health — Z-Mesh servis saglik kontrolu
# Kullanim: bash mesh-health.sh
# NOT: Bu script localhost PORT YOKLAMAZ. Gercek WG IP'lerini kullanir.
# avm-01 uzerinde calistirilmali (wg0 = 10.10.0.1)
set -uo pipefail

AVM02="10.10.0.2"
SECRETS_FILE="/run/hermes/secrets.env"

# Secret dosyasi varsa MEMORY_BEARER oku
KEY="MISSING"
if [[ -r "$SECRETS_FILE" ]]; then
  KEY=$(grep -m1 '^MEMORY_BEARER=' "$SECRETS_FILE" 2>/dev/null | cut -d= -f2- || \
        grep -m1 '^MEMORY_CORE_API_KEY=' "$SECRETS_FILE" 2>/dev/null | cut -d= -f2-)
  KEY=${KEY:-MISSING}
fi

# WG mesh ping
wg_status=$(ping -c1 -W2 "$AVM02" >/dev/null 2>&1 && echo "OK" || echo "DOWN")

# n8n health
n8n_code=$(curl -sf -m 5 -o /dev/null -w '%{http_code}' \
  "http://${AVM02}:5678/healthz" 2>/dev/null || echo "FAIL")

# memory-core health (auth required)
mc_code=$(curl -sf -m 5 -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer ${KEY}" \
  "http://${AVM02}:8848/healthz" 2>/dev/null || echo "FAIL")

# MEMORY.md stale check
MEMORY_MD="${HOME}/.hermes/memories/MEMORY.md"
if [[ -f "$MEMORY_MD" ]]; then
  last_write=$(stat -c %Y "$MEMORY_MD" 2>/dev/null || echo 0)
  age_h=$(( ( $(date +%s) - last_write ) / 3600 ))
  stale_warn=""
  [[ $age_h -gt 2 ]] && stale_warn=" ⚠ STALE (${age_h}h)"
else
  age_h="?"
  stale_warn=" ⚠ MEMORY.md NOT FOUND"
fi

# Ozet
echo "[$(date -Iseconds)] mesh-health report"
echo "  wg-mesh (${AVM02}): ${wg_status}"
echo "  n8n     :5678     : HTTP ${n8n_code}"
echo "  memory-core :8848 : HTTP ${mc_code}  [key=$([ "$KEY" = "MISSING" ] && echo 'MISSING' || echo 'SET')]"
echo "  MEMORY.md age     : ${age_h}h${stale_warn}"

# Cikis kodu: 0=tamam, 1=sorun var
if [[ "$wg_status" == "OK" && "$n8n_code" == "200" && "$mc_code" == "200" ]]; then
  echo "  STATUS: ✅ ALL GREEN"
  exit 0
else
  echo "  STATUS: ❌ DEGRADED"
  exit 1
fi
