# --- Stage 1: build the Vue frontend ---
FROM node:26-alpine AS frontend
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
# `git` is needed only to RESOLVE the pinned foodcore requirement, so it is
# installed and purged inside one layer — leaving it in the image would ship a
# build tool to every user for no runtime purpose. foodcore itself has zero
# runtime dependencies, so nothing else arrives with it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir -r backend/requirements.txt \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

# foodcore lives in its own repository and is pinned to a commit in
# backend/requirements.txt, so there is no copy of it in this tree: nothing for
# an accidental sys.path reach to find (its source dir is `app/` and would
# shadow this app's own `app` package), and no way for this repo to drift ahead
# of the other consumer. The reference dataset travels inside the wheel.
#
# The check below asserts the ARTIFACT, not just that an import succeeds. This
# library has already shipped a wheel that imported cleanly while missing a
# subpackage, and its reference dataset is package DATA that a pyproject change
# can silently drop. Both failures present identically at runtime — the feature
# quietly does nothing — and neither is visible from a source checkout, where
# the files are simply there on disk.
#
# `household_v1` is checked by CALLING it, not merely importing it: it is now
# the single most load-bearing thing this app asks of the library (it is what
# answers "2 cups flour"), it degrades silently to the hand-written density
# table, and it is a dict whose contents are the contract — an import proves
# the module exists, not that "flour" is still in it.
RUN python3 -c "import foodcore, os, gzip; \
p = os.path.join(os.path.dirname(foodcore.__file__), 'data', 'usda-sr-legacy-2018-04.sqlite.gz'); \
assert os.path.isfile(p), 'the shipped reference dataset is missing from the installed wheel'; \
assert len(gzip.open(p, 'rb').read(64)) == 64, 'the shipped reference dataset is not readable gzip'; \
__import__('foodcore.rules.rulesets', fromlist=['x']).allergen_ruleset(); \
assert __import__('foodcore.rules.household_v1', fromlist=['x']).household_source_id('flour'); \
print('foodcore', foodcore.__version__, '+ dataset', round(os.path.getsize(p) / 1e6, 1), 'MB')"

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
