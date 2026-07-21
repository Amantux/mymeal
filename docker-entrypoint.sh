#!/usr/bin/env sh
# Unified entrypoint: standalone Docker and Home Assistant add-on.
#
#   1. Validate configuration BEFORE anything starts, so a bad setting is a
#      clear startup error rather than a confusing 500 on a later request.
#   2. Start the optional MCP server and supervise it.
#   3. Run gunicorn, forwarding signals so shutdown is clean.
#
# options.json is deliberately NOT parsed here. The application reads it via
# one tested adapter (app.settings.load_ha_options), so the shell and Python
# cannot disagree about what the configuration is. Previously four separate
# `python3 -c` invocations re-parsed the same file and duplicated its defaults.
set -e

cd /app/backend

: "${MYMEAL_DATA_DIR:=/data}"
export MYMEAL_DATA_DIR
mkdir -p "$MYMEAL_DATA_DIR"

# NOTE: MYMEAL_SECRET_KEY is intentionally NOT defaulted here. It used to be
# `head -c 32 /dev/urandom`, which minted a NEW signing key on every container
# start — silently logging every user out and voiding every issued API token on
# each restart. The application now generates one once and persists it under
# MYMEAL_DATA_DIR instead.

# ---------------------------------------------------------------------------
# 1. Validate. Prints every problem at once; in the add-on this lands in the
#    Log tab, which is where an operator will actually look.
# ---------------------------------------------------------------------------
if ! python3 -m app.config_check; then
  echo "myMeal: refusing to start with invalid configuration (see above)." >&2
  exit 1
fi

# Read back the values the shell needs, from the SAME source of truth.
eval "$(python3 - <<'PY'
from app.settings import load_settings
s = load_settings()
print(f"RESOLVED_PORT={s.PORT}")
print(f"RESOLVED_MCP_ENABLED={'true' if s.MCP_ENABLED else 'false'}")
print(f"RESOLVED_MCP_PORT={s.MCP_PORT}")
print(f"RESOLVED_MCP_API={s.mcp_api}")
print(f"RESOLVED_WORKERS={s.WORKERS}")
print(f"RESOLVED_THREADS={s.THREADS}")
print(f"RESOLVED_TIMEOUT={s.TIMEOUT}")
print(f"RESOLVED_GRACEFUL={s.GRACEFUL_TIMEOUT}")
print(f"RESOLVED_LOGLEVEL={s.LOG_LEVEL.lower()}")
PY
)"

# Best-effort Home Assistant discovery. Failure is logged, not silenced with
# `|| true`: discovery is a convenience, but a silent failure is a mystery.
python3 ha_discovery.py \
  || echo "myMeal: HA discovery registration skipped (not running under Supervisor)."

# ---------------------------------------------------------------------------
# 2. MCP server. Previously backgrounded and then orphaned by `exec gunicorn`,
#    so a crashed MCP was invisible and SIGTERM never reached it. We now keep a
#    supervising shell that forwards signals and reports an MCP exit.
# ---------------------------------------------------------------------------
MCP_PID=""
if [ "$RESOLVED_MCP_ENABLED" = "true" ] && [ -f mcp_server.py ]; then
  # The MCP server token is a SECRET, so capture it with command substitution
  # (data, never eval'd) rather than through the RESOLVED_* eval block above —
  # an operator-controlled value must not be able to inject shell.
  # `|| true` so a hiccup reading the (optional) token never aborts startup
  # under `set -e` — the MCP server is optional and must not take the app down.
  MCP_SERVER_TOKEN="$(python3 -c 'from app.settings import load_settings; print(load_settings().MCP_SERVER_TOKEN)' 2>/dev/null || true)"
  MYMEAL_MCP_API="$RESOLVED_MCP_API" \
    MYMEAL_MCP_PORT="$RESOLVED_MCP_PORT" \
    MYMEAL_MCP_SERVER_TOKEN="$MCP_SERVER_TOKEN" \
    python3 mcp_server.py &
  MCP_PID=$!
  echo "myMeal: MCP server started (pid $MCP_PID) on :${RESOLVED_MCP_PORT}/sse"
fi

GUNICORN_PID=""
shutdown() {
  [ -n "$GUNICORN_PID" ] && kill -TERM "$GUNICORN_PID" 2>/dev/null || true
  [ -n "$MCP_PID" ] && kill -TERM "$MCP_PID" 2>/dev/null || true
  wait
  exit 0
}
trap shutdown TERM INT

# ---------------------------------------------------------------------------
# 3. Gunicorn, configured from the validated contract rather than hardcoded
#    flags. Worker count stays low on purpose: SQLite serialises writes, so
#    extra workers add lock contention; threads absorb concurrent readers.
# ---------------------------------------------------------------------------
gunicorn \
  --bind "0.0.0.0:${RESOLVED_PORT}" \
  --workers "$RESOLVED_WORKERS" \
  --threads "$RESOLVED_THREADS" \
  --timeout "$RESOLVED_TIMEOUT" \
  --graceful-timeout "$RESOLVED_GRACEFUL" \
  --log-level "$RESOLVED_LOGLEVEL" \
  --access-logfile - \
  --error-logfile - \
  "app:create_app()" &
GUNICORN_PID=$!
echo "myMeal: gunicorn on :${RESOLVED_PORT} (${RESOLVED_WORKERS} workers, ${RESOLVED_THREADS} threads)"

# Supervise. A dead MCP is reported rather than silently absent; a dead
# gunicorn takes the container down with its real exit status.
while true; do
  if [ -n "$MCP_PID" ] && ! kill -0 "$MCP_PID" 2>/dev/null; then
    echo "myMeal: WARNING - MCP server (pid $MCP_PID) exited. Assist voice control" \
         "is unavailable; the web app is unaffected. Set MYMEAL_MCP_REQUIRED=true" \
         "to make this fail readiness." >&2
    MCP_PID=""
  fi
  if ! kill -0 "$GUNICORN_PID" 2>/dev/null; then
    wait "$GUNICORN_PID"
    status=$?
    echo "myMeal: gunicorn exited with status $status; shutting down." >&2
    [ -n "$MCP_PID" ] && kill -TERM "$MCP_PID" 2>/dev/null || true
    exit "$status"
  fi
  sleep 5
done
