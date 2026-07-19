#!/usr/bin/env sh
# Unified entrypoint: works standalone and as a Home Assistant add-on.
set -e

OPTIONS=/data/options.json

# When running as an HA add-on, translate options.json into env vars.
if [ -f "$OPTIONS" ]; then
  MYMEAL_DISABLE_AUTH="$(python3 -c "import json;print(str(json.load(open('$OPTIONS')).get('disable_auth', True)).lower())")"
  MYMEAL_ALLOW_REGISTRATION="$(python3 -c "import json;print(str(json.load(open('$OPTIONS')).get('allow_registration', False)).lower())")"
  MYMEAL_MCP_ENABLED="$(python3 -c "import json;print(str(json.load(open('$OPTIONS')).get('enable_mcp', True)).lower())")"
  MYMEAL_AI_PROVIDER="$(python3 -c "import json;print(json.load(open('$OPTIONS')).get('ai_provider', ''))")"
  export MYMEAL_DISABLE_AUTH MYMEAL_ALLOW_REGISTRATION MYMEAL_MCP_ENABLED MYMEAL_AI_PROVIDER
fi

# Sensible defaults.
: "${MYMEAL_DATA_DIR:=/data}"
: "${MYMEAL_DISABLE_AUTH:=false}"
: "${MYMEAL_SECRET_KEY:=$(head -c 32 /dev/urandom | base64)}"
: "${MYMEAL_PORT:=7850}"
: "${MYMEAL_MCP_ENABLED:=true}"
: "${MYMEAL_MCP_PORT:=7851}"
export MYMEAL_DATA_DIR MYMEAL_DISABLE_AUTH MYMEAL_SECRET_KEY MYMEAL_PORT MYMEAL_MCP_PORT

mkdir -p "$MYMEAL_DATA_DIR"

cd /app/backend

# Best-effort Home Assistant discovery registration (no-op outside HA).
python3 ha_discovery.py || true

# MCP server for Home Assistant — runs alongside the app in this same container,
# talking to the local API. Exposes an SSE endpoint on MYMEAL_MCP_PORT.
# (Shipped from the MCP milestone onward; guarded so earlier builds still boot.)
if [ "${MYMEAL_MCP_ENABLED}" = "true" ] && [ -f mcp_server.py ]; then
  MYMEAL_MCP_API="http://127.0.0.1:${MYMEAL_PORT}/api/v1" \
    python3 mcp_server.py &
  echo "myMeal MCP server started on :${MYMEAL_MCP_PORT}/sse"
fi

exec gunicorn -b "0.0.0.0:${MYMEAL_PORT}" -w 2 --timeout 120 "app:create_app()"
