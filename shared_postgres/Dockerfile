FROM postgres:18

# A small provisioning sidecar (Flask + psycopg2) mints a per-app database+role
# on request and publishes Supervisor discovery. Installed from Debian packages
# so no pip/network wheel build is needed on-device.
#
# postgresql-16 is here for pg_upgrade ONLY: it needs the OLD major's binaries to
# read a cluster written by the previous add-on version. Without it every
# existing install would come up to a data directory PostgreSQL 18 refuses to
# open. Costs ~40 MiB, and can be dropped once nobody is upgrading from 16.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-flask python3-psycopg2 postgresql-16 \
    && rm -rf /var/lib/apt/lists/*

COPY provisioner.py /opt/provisioner.py
COPY run.sh /run.sh
COPY pg-upgrade.sh /opt/pg-upgrade.sh
RUN chmod +x /run.sh /opt/pg-upgrade.sh

# Persist the cluster in the add-on's /data (survives restarts/updates).
ENV PGDATA=/data/pgdata

CMD ["/run.sh"]
