#!/usr/bin/env bash
# Claude CLI memory hook — shared-memory recall (context injection) + ingest,
# routed through mesh_router.py (REST -> memory-core gateway).
#
# Wire into ~/.claude/settings.json hooks, e.g.:
#   UserPromptSubmit -> memory_hook.sh recall     (injects relevant memory)
#   Stop             -> memory_hook.sh ingest      (persists a session note)
#
# Contract: this hook NEVER breaks a session. Any failure (gateway down, no
# python, bad input) exits 0 with no output, so the original prompt passes
# through unchanged ("memory-less mode").
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROUTER="$(cd "$SCRIPT_DIR/.." && pwd)/mesh_router.py"
PY="${ZMEMORY_PYTHON:-python3}"
mode="${1:-recall}"

case "$mode" in
  recall)
    # Query text precedence: $2 arg > `prompt`/`query` field on stdin JSON.
    q="${2:-}"
    if [ -z "$q" ] && [ ! -t 0 ]; then
      q="$("$PY" -c 'import sys,json
try:
    d=json.load(sys.stdin); print(d.get("prompt") or d.get("query") or "")
except Exception:
    print("")' 2>/dev/null)"
    fi
    # --inject prints a MEMORY-MESH block on hits, nothing when empty.
    "$PY" "$ROUTER" query ${q:+"$q"} --inject --limit "${ZMEMORY_RECALL_LIMIT:-6}" 2>/dev/null || true
    ;;

  ingest)
    text="${2:-}"
    if [ -z "$text" ] && [ ! -t 0 ]; then text="$(cat)"; fi
    [ -z "$text" ] && exit 0
    "$PY" "$ROUTER" store "$text" \
      --scope "${ZMEMORY_SCOPE:-cli-claude-code}" \
      --kind "${ZMEMORY_KIND:-capture}" \
      --source "claude-cli-hook" \
      ${3:+--title "$3"} >/dev/null 2>&1 || true
    ;;

  health)
    "$PY" "$ROUTER" health
    ;;

  *)
    echo "usage: memory_hook.sh {recall [query] | ingest [text] [title] | health}" >&2
    exit 2
    ;;
esac
exit 0
