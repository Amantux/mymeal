FROM postgres:16

# A small provisioning sidecar (Flask + psycopg2) mints a per-app database+role
# on request and publishes Supervisor discovery. Installed from Debian packages
# so no pip/network wheel build is needed on-device.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-flask python3-psycopg2 \
    && rm -rf /var/lib/apt/lists/*

COPY provisioner.py /opt/provisioner.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

# Persist the cluster in the add-on's /data (survives restarts/updates).
ENV PGDATA=/data/pgdata

CMD ["/run.sh"]
