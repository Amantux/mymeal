# --- Stage 1: build the Vue frontend ---
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: python runtime ---
FROM python:3.14-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    MYMEAL_DATA_DIR=/data \
    MYMEAL_FRONTEND_DIST=/app/frontend/dist \
    MYMEAL_PORT=7850 \
    # gosu does not reset HOME; without this it stays /root and anything
    # writing ~/.cache (pip, httpx trust_env, tokenizers) hits EACCES as uid 1000.
    HOME=/home/app

# gosu lets the entrypoint do its privileged setup (read the HA add-on's
# options.json, fix /data ownership) as root, then drop to a non-root user for
# the actual server processes. Matches HomeHoard/Edibl so all three add-ons
# share one privilege model.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -u 1000 -m -d /home/app app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY --from=frontend /build/dist ./frontend/dist
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /data \
    && chown -R app:app /app /data

# No `USER app` here on purpose (this is why Trivy DS-0002 is dismissed, not
# fixed): the container must start as root to chown the Home Assistant-managed
# /data volume, whose ownership we don't control. docker-entrypoint.sh does that
# one privileged step and then runs EVERY long-running process (gunicorn, the MCP
# server, provisioning, discovery) as the unprivileged `app` user via gosu — see
# RUN_AS there. Same model as HomeHoard/Edibl.
VOLUME ["/data"]
EXPOSE 7850

# The entrypoint works both standalone and as a Home Assistant add-on
# (it reads /data/options.json and registers Supervisor discovery when present).
CMD ["/app/docker-entrypoint.sh"]
