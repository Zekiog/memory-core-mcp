#!/usr/bin/env bash
# Polls /readyz; on degradation (non-200), logs a CRIT line to the journal
# (guaranteed available -- no assumption about NATS/node_exporter being
# configured) and, best-effort, writes a node_exporter textfile-collector
# gauge if that directory exists, so Grafana picks it up without extra wiring.
set -euo pipefail

URL="http://127.0.0.1:8848/readyz"
TEXTFILE_DIR="/var/lib/node_exporter/textfile_collector"

status=$(curl -s -o /dev/null -w '%{http_code}' "$URL" || echo "000")

if [ "$status" = "200" ]; then
  mode_ok=1
else
  mode_ok=0
  logger -p user.crit -t memory-core-readyz \
    "embedding degraded: GET $URL returned $status (expected 200)"
fi

if [ -d "$TEXTFILE_DIR" ] && [ -w "$TEXTFILE_DIR" ]; then
  tmp=$(mktemp "$TEXTFILE_DIR/.memory_core_ready.XXXXXX")
  printf 'memory_core_ready %s\n' "$mode_ok" > "$tmp"
  mv "$tmp" "$TEXTFILE_DIR/memory_core_ready.prom"
fi

exit 0
