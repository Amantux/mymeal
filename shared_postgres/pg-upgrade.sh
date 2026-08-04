#!/usr/bin/env bash
# In-place major-version upgrade of the persisted cluster in /data/pgdata.
#
# The add-on keeps its cluster in the Supervisor-managed /data so it survives
# restarts and updates. PostgreSQL will NOT start against a data directory
# written by an older major version, so bumping the base image without this
# would leave every existing install with intact-but-unreadable data.
#
# Called by run.sh before Postgres starts. A no-op on a fresh install and on an
# already-current cluster, so it costs nothing in the normal case.
#
# Design rules, in order of importance:
#
#   1. NEVER destroy the old cluster. pg_upgrade runs in copy mode (not --link),
#      and the old directory is renamed aside, not deleted. If anything goes
#      wrong the operator still has a working PG16 data directory.
#   2. Fail loudly and refuse to start, rather than let Postgres touch a data
#      directory it cannot read. A container that exits with an explanation is
#      recoverable; one that half-starts is not.
#   3. Every precondition is checked BEFORE anything is written: old binaries
#      present, enough free disk, cluster cleanly shut down.
set -euo pipefail

PGDATA="${PGDATA:?PGDATA must be set}"
NEW_MAJOR="${PG_MAJOR:?PG_MAJOR must be set}"      # set by the postgres base image
DATA_ROOT="$(dirname "$PGDATA")"

log() { echo "[pg-upgrade] $*"; }
die() { echo "[pg-upgrade] FATAL: $*" >&2; exit 1; }

# Fresh install — the entrypoint will initdb it at the current major.
[ -s "$PGDATA/PG_VERSION" ] || { log "no existing cluster; nothing to upgrade"; exit 0; }

OLD_MAJOR="$(tr -d '[:space:]' < "$PGDATA/PG_VERSION")"
if [ "$OLD_MAJOR" = "$NEW_MAJOR" ]; then
  log "cluster is already PostgreSQL $NEW_MAJOR; nothing to do"
  exit 0
fi

# Downgrades are not a thing pg_upgrade can do. Refusing is the only safe answer.
if [ "$OLD_MAJOR" -gt "$NEW_MAJOR" ] 2>/dev/null; then
  die "data directory is PostgreSQL $OLD_MAJOR but this image is $NEW_MAJOR.
     Downgrading is not supported. Reinstall the previous add-on version."
fi

OLD_BIN="/usr/lib/postgresql/$OLD_MAJOR/bin"
NEW_BIN="/usr/lib/postgresql/$NEW_MAJOR/bin"
[ -x "$OLD_BIN/pg_ctl" ] || die "no PostgreSQL $OLD_MAJOR binaries in this image.
     pg_upgrade needs the OLD major's binaries to read the existing cluster.
     Add postgresql-$OLD_MAJOR to the image, or restore the previous add-on."
[ -x "$NEW_BIN/pg_upgrade" ] || die "no pg_upgrade in $NEW_BIN"

log "upgrading cluster: PostgreSQL $OLD_MAJOR -> $NEW_MAJOR"

# --- preconditions -----------------------------------------------------------

# Copy mode needs room for a second copy of the cluster. Checked up front,
# because running out of disk half-way through is the one failure that is
# genuinely messy to recover from.
need_kb="$(du -sk "$PGDATA" | cut -f1)"
free_kb="$(df -Pk "$DATA_ROOT" | awk 'NR==2 {print $4}')"
log "cluster is $((need_kb / 1024)) MiB; $((free_kb / 1024)) MiB free on $DATA_ROOT"
if [ "$free_kb" -lt "$((need_kb + need_kb / 5))" ]; then
  die "not enough free space to copy the cluster (need ~$((need_kb * 12 / 10 / 1024)) MiB,
     have $((free_kb / 1024)) MiB). Free space in /data and restart the add-on."
fi

NEW_DATA="$DATA_ROOT/pgdata-$NEW_MAJOR"
OLD_KEEP="$DATA_ROOT/pgdata-old-$OLD_MAJOR"
[ -e "$OLD_KEEP" ] && die "$OLD_KEEP already exists — a previous upgrade left it behind.
     Move or remove it once you are satisfied the current cluster is healthy."
rm -rf "$NEW_DATA"

# pg_upgrade refuses a cluster that was not shut down cleanly (e.g. the add-on
# was killed). Starting the OLD server and stopping it cleanly performs crash
# recovery and leaves the state pg_upgrade requires.
#
# While it is up, read the settings the new cluster has to match. They are NOT
# in pg_controldata — encoding and locale are per-database and live in
# pg_database, so an earlier version of this script silently fell back to
# initdb's defaults and only worked because the test cluster happened to use
# them. template0 is the one pg_upgrade compares.
SOCK="$(mktemp -d)"
log "starting the old cluster briefly (clean shutdown + read its locale)"
"$OLD_BIN/pg_ctl" -D "$PGDATA" -w -t 120 \
  -o "-c listen_addresses='' -p 5433 -k $SOCK" start >/dev/null 2>&1 || true

oldq() { "$OLD_BIN/psql" -h "$SOCK" -p 5433 -U postgres -d postgres -tAc "$1" 2>/dev/null || true; }
ENCODING="$(oldq "select pg_encoding_to_char(encoding) from pg_database where datname='template0'")"
COLLATE="$(oldq  "select datcollate from pg_database where datname='template0'")"
CTYPE="$(oldq    "select datctype   from pg_database where datname='template0'")"

"$OLD_BIN/pg_ctl" -D "$PGDATA" -w -t 120 -m fast stop >/dev/null 2>&1 || true
rmdir "$SOCK" 2>/dev/null || true

# --- match the new cluster to the old ---------------------------------------
# pg_upgrade compares these and aborts on any mismatch. The checksum setting is
# the other real trap: PG18's initdb turns data checksums ON by default while
# PG16's default is OFF.
ctl() { "$OLD_BIN/pg_controldata" "$PGDATA" | awk -F: -v k="$1" '$0 ~ k {sub(/^[^:]*: */,""); gsub(/^ +| +$/,""); print; exit}'; }
CHECKSUMS="$(ctl 'Data page checksum version')"

# If the old server would not start we cannot read its locale, and guessing is
# how you get a cluster that pg_upgrade rejects half-way through. Stop instead.
[ -n "$ENCODING" ] || die "could not read the old cluster's encoding — it would not start.
     Refusing to guess. The existing cluster at $PGDATA is untouched."

INITDB_ARGS=(--username=postgres --auth-local=trust --auth-host=scram-sha-256)
INITDB_ARGS+=("--encoding=$ENCODING")
[ -n "$COLLATE" ] && INITDB_ARGS+=("--lc-collate=$COLLATE")
[ -n "$CTYPE" ]   && INITDB_ARGS+=("--lc-ctype=$CTYPE")
if [ "$CHECKSUMS" = "0" ]; then
  # Load-bearing on 16 -> 18: without this the new cluster has checksums on,
  # the old one does not, and pg_upgrade aborts.
  INITDB_ARGS+=(--no-data-checksums)
else
  INITDB_ARGS+=(--data-checksums)
fi
log "new cluster: encoding=${ENCODING:-default} collate=${COLLATE:-default} checksums=$CHECKSUMS"

"$NEW_BIN/initdb" -D "$NEW_DATA" "${INITDB_ARGS[@]}" >/dev/null

# --- the upgrade itself ------------------------------------------------------
# Run from a scratch directory: pg_upgrade writes its logs into $PWD and the
# data root should not collect them.
WORK="$(mktemp -d)"
cd "$WORK"
log "running pg_upgrade (copy mode; the old cluster is left intact)"
if ! "$NEW_BIN/pg_upgrade" \
      --old-bindir="$OLD_BIN" --new-bindir="$NEW_BIN" \
      --old-datadir="$PGDATA" --new-datadir="$NEW_DATA" \
      --username=postgres >"$WORK/pg_upgrade.out" 2>&1; then
  echo "--- pg_upgrade output ---" >&2
  tail -40 "$WORK/pg_upgrade.out" >&2 || true
  for f in "$WORK"/*.log "$WORK"/pg_upgrade_output.d/*/*.log; do
    [ -f "$f" ] && { echo "--- $f ---" >&2; tail -20 "$f" >&2; }
  done
  rm -rf "$NEW_DATA"
  die "pg_upgrade failed. The existing PostgreSQL $OLD_MAJOR cluster is UNCHANGED
     at $PGDATA — reinstall the previous add-on version to keep running."
fi

# --- swap, keeping the old cluster ------------------------------------------
log "upgrade succeeded; swapping in the new cluster"
mv "$PGDATA" "$OLD_KEEP"
mv "$NEW_DATA" "$PGDATA"

# Carry over the operator's own configuration. The upgraded cluster gets a fresh
# postgresql.conf/pg_hba.conf from initdb, so anything hand-edited would be
# silently reverted.
for f in postgresql.conf pg_hba.conf pg_ident.conf; do
  if [ -f "$OLD_KEEP/$f" ]; then
    cp -a "$OLD_KEEP/$f" "$PGDATA/$f.from-$OLD_MAJOR"
  fi
done

log "done. PostgreSQL $NEW_MAJOR cluster is live."
log "the previous $OLD_MAJOR cluster is kept at $OLD_KEEP — delete it once you are"
log "satisfied everything works; it is the only rollback path."
log "your previous config files were copied alongside as *.from-$OLD_MAJOR (not applied)."
