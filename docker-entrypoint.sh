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

# Drop privileges: the container starts as root so it can create/own the HA
# add-on's /data, then every long-running process runs as the unprivileged `app`
# user via gosu. If we're already non-root (some runtimes), RUN_AS stays empty
# and commands run directly. Matches HomeHoard/Edibl.
RUN_AS=""
if [ "$(id -u)" = "0" ]; then
  # A root-owned /data from a previous release is fixed here (root can always
  # chown). The failure case is a read-only or non-chownable mount (ro bind,
  # some SMB/NFS-backed setups): the chown no-ops, we drop to uid 1000, and the
  # first request dies with "unable to open database file" hours later with no
  # explanation. So verify writability AS the app user and fail loudly now.
  chown -R app:app "$MYMEAL_DATA_DIR" 2>/dev/null || true
  if ! gosu app test -w "$MYMEAL_DATA_DIR"; then
    echo "myMeal: $MYMEAL_DATA_DIR is not writable by the 'app' user (uid 1000)." >&2
    echo "        The data directory must be writable by the container's app user." >&2
    exit 1
  fi
  RUN_AS="gosu app"
fi

# NOTE: MYMEAL_SECRET_KEY is intentionally NOT defaulted here. It used to be
# `head -c 32 /dev/urandom`, which minted a NEW signing key on every container
# start — silently logging every user out and voiding every issued API token on
# each restart. The application now generates one once and persists it under
# MYMEAL_DATA_DIR instead.

# ---------------------------------------------------------------------------
# 1. Validate. Prints every problem at once; in the add-on this lands in the
#    Log tab, which is where an operator will actually look.
# ---------------------------------------------------------------------------
if ! $RUN_AS python3 -m app.config_check; then
  echo "myMeal: refusing to start with invalid configuration (see above)." >&2
  exit 1
fi

# Read back the values the shell needs, from the SAME source of truth.
eval "$($RUN_AS python3 - <<'PY'
from app.settings import load_settings
s = load_settings()
print(f"RESOLVED_PORT={s.PORT}")
print(f"RESOLVED_MCP_ENABLED={'true' if s.MCP_ENABLED else 'false'}")
print(f"RESOLVED_MCP_EXPOSE_EXTERNAL={'true' if s.MCP_EXPOSE_EXTERNAL else 'false'}")
print(f"RESOLVED_MCP_PORT={s.MCP_PORT}")
print(f"RESOLVED_MCP_API={s.mcp_api}")
print(f"RESOLVED_WORKERS={s.WORKERS}")
print(f"RESOLVED_THREADS={s.THREADS}")
print(f"RESOLVED_TIMEOUT={s.TIMEOUT}")
print(f"RESOLVED_GRACEFUL={s.GRACEFUL_TIMEOUT}")
print(f"RESOLVED_LOGLEVEL={s.LOG_LEVEL.lower()}")
PY
)"

# Shared PostgreSQL: when enabled, discover the add-on and provision our own
# database (writes the DSN to /data/.database_url, which the app reads). Runs
# before gunicorn so the engine is built against the right database. Best-effort
# — it self-selects SQLite if anything is missing, so it never blocks startup.
$RUN_AS python3 -m app.pg_provision \
  || echo "myMeal: shared-PostgreSQL provisioning skipped."

# Best-effort Home Assistant discovery. Failure is logged, not silenced with
# `|| true`: discovery is a convenience, but a silent failure is a mystery.
$RUN_AS python3 ha_discovery.py \
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
  MCP_SERVER_TOKEN="$($RUN_AS python3 -c 'from app.settings import load_settings; print(load_settings().MCP_SERVER_TOKEN)' 2>/dev/null || true)"
  MYMEAL_MCP_API="$RESOLVED_MCP_API" \
    MYMEAL_MCP_PORT="$RESOLVED_MCP_PORT" \
    MYMEAL_MCP_SERVER_TOKEN="$MCP_SERVER_TOKEN" \
    MYMEAL_MCP_EXPOSE_EXTERNAL="$RESOLVED_MCP_EXPOSE_EXTERNAL" \
    MYMEAL_PROC=mcp $RUN_AS python3 mcp_server.py &
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
$RUN_AS gunicorn \
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
