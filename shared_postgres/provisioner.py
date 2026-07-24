"""Provisioning sidecar for the Shared PostgreSQL add-on.

Runs beside Postgres in the same container. It:
  * publishes a Supervisor discovery message so sibling add-ons (myMeal, Edibl,
    HomeHoard) can find us and read {host, port, provision_url, token}; and
  * exposes POST /provision, which hands each app its OWN database + a
    least-privilege login role (never the superuser), idempotently.

Auth: the ``provision_token`` add-on option. Left blank (default), the endpoint
is open on the internal hassio network (ports are internal-only) — the simple
zero-config path. Set it to require the token (and siblings receive it via the
discovery message). The sidecar reaches the local cluster over TCP as the
superuser using the password run.sh set; siblings use their own credentials.
"""
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.request

import psycopg2
from flask import Flask, jsonify, request

DATA_DIR = "/data"
OPTIONS_FILE = os.path.join(DATA_DIR, "options.json")
APPS_FILE = os.path.join(DATA_DIR, "apps.json")
SUPERVISOR = "http://supervisor"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
PG_PORT = 5432
API_PORT = 8087
APP_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")  # also guards SQL identifier safety

flask_app = Flask(__name__)


def _load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


# Blank token = open on the trusted internal network (default). Set to require.
TOKEN = str(_load_json(OPTIONS_FILE, {}).get("provision_token") or "").strip()


def _superuser_conn():
    # PG_CONNECT_PORT lets tests point at a mapped port; in the add-on Postgres
    # is on PG_PORT (5432) in-container.
    conn = psycopg2.connect(
        host="127.0.0.1",
        port=int(os.environ.get("PG_CONNECT_PORT", str(PG_PORT))),
        dbname="postgres", user="postgres",
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    conn.autocommit = True  # CREATE DATABASE cannot run inside a transaction
    return conn


def _wait_for_pg(timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _superuser_conn().close()
            return True
        except psycopg2.Error:
            time.sleep(1)
    return False


def _self_hostname():
    if SUPERVISOR_TOKEN:
        try:
            req = urllib.request.Request(
                f"{SUPERVISOR}/addons/self/info",
                headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read() or b"{}").get("data", {})
                if data.get("hostname"):
                    return data["hostname"]
        except Exception as exc:  # noqa: BLE001 - best effort
            print(f"shared_postgres: self/info failed: {exc}")
    return socket.gethostname()


def _register_discovery(host):
    if not SUPERVISOR_TOKEN:
        return
    payload = json.dumps({
        "service": "shared_postgres",
        "config": {
            "host": host,
            "port": PG_PORT,
            "provision_url": f"http://{host}:{API_PORT}/provision",
            "token": TOKEN,
        },
    }).encode()
    req = urllib.request.Request(
        f"{SUPERVISOR}/discovery", data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
        print("shared_postgres: discovery registered")
    except Exception as exc:  # noqa: BLE001 - best effort, never block serving
        print(f"shared_postgres: discovery registration failed (non-fatal): {exc}")


def _startup():
    if _wait_for_pg():
        _register_discovery(_self_hostname())
    else:
        print("shared_postgres: Postgres not ready in time; API up, discovery skipped")


def _authenticated(req) -> bool:
    if not TOKEN:
        return True  # open on the internal network (blank token)
    header = req.headers.get("Authorization", "")
    presented = header[7:].strip() if header.startswith("Bearer ") else req.args.get("token", "")
    return secrets.compare_digest(presented or "", TOKEN)


@flask_app.get("/health")
def health():
    return jsonify({"ok": True})


@flask_app.post("/provision")
def provision():
    if not _authenticated(request):
        return jsonify({"error": "unauthorized"}), 401
    name = str((request.get_json(silent=True) or {}).get("app", "")).strip().lower()
    if not APP_RE.match(name):
        return jsonify({"error": "invalid app name"}), 400

    apps = _load_json(APPS_FILE, {})
    new = name not in apps
    password = secrets.token_urlsafe(24) if new else apps[name]  # url-safe: no %/@ to encode

    conn = _superuser_conn()
    cur = conn.cursor()
    # name is validated to [a-z][a-z0-9_]* so quoting the identifier is safe.
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    if cur.fetchone():
        cur.execute(f'ALTER ROLE "{name}" WITH LOGIN PASSWORD %s', (password,))
    else:
        cur.execute(f'CREATE ROLE "{name}" WITH LOGIN PASSWORD %s', (password,))
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    if not cur.fetchone():
        cur.execute(f'CREATE DATABASE "{name}" OWNER "{name}"')
    # Isolate: by default PUBLIC may CONNECT to any database, so one app could
    # open another's. Restrict CONNECT to the owning role. Applied every time so
    # the ACL is correct even for a pre-existing database.
    cur.execute(f'REVOKE CONNECT ON DATABASE "{name}" FROM PUBLIC')
    cur.execute(f'GRANT ALL PRIVILEGES ON DATABASE "{name}" TO "{name}"')
    cur.close()
    conn.close()

    if new:
        apps[name] = password
        fd = os.open(APPS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(apps, fh)

    host = _self_hostname()
    return jsonify({
        "dsn": f"postgresql+psycopg://{name}:{password}@{host}:{PG_PORT}/{name}",
        "host": host, "port": PG_PORT, "database": name, "user": name,
    })


threading.Thread(target=_startup, daemon=True).start()

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=API_PORT)
