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
