#!/usr/bin/env bash
# Bridge wrapper so Claude Desktop (a GUI app that inherits no shell env) can
# connect to the bearer-authed remote MCP without committing the token.
#
# It sources the local secret file and execs `mcp-remote` against the gateway's
# streamable-http MCP endpoint (/mcp). Referenced from mcp_config.json by path.
set -euo pipefail

ENV_FILE="${ZMEMORY_ENV_FILE:-$HOME/.secrets.d/zmemory-mesh.env}"
if [ -r "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

: "${ZMEMORY_GATEWAY_URL:?set ZMEMORY_GATEWAY_URL (or provide $ENV_FILE)}"
: "${ZMEMORY_BEARER_TOKEN:?set ZMEMORY_BEARER_TOKEN (or provide $ENV_FILE)}"

exec npx -y mcp-remote "${ZMEMORY_GATEWAY_URL%/}/mcp" \
  --header "Authorization: Bearer ${ZMEMORY_BEARER_TOKEN}"
