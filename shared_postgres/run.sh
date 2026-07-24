#!/usr/bin/env bash
# Start the provisioning sidecar (background) and Postgres (foreground). Postgres
# is the main process, so if it exits the Supervisor restarts the add-on.
set -e

export PGDATA=/data/pgdata

# Give the superuser a strong, persisted password so TCP connections require
# auth. The sidecar connects over localhost with this same password; sibling
# apps connect with their own provisioned per-app credentials.
PW_FILE=/data/.superuser_password
if [ ! -f "$PW_FILE" ]; then
  (umask 077; head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32 > "$PW_FILE")
fi
POSTGRES_PASSWORD="$(cat "$PW_FILE")"
export POSTGRES_PASSWORD

python3 /opt/provisioner.py &

exec docker-entrypoint.sh postgres
