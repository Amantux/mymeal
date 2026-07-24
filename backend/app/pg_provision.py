"""Find + register with the Shared PostgreSQL add-on (Phase 3).

When ``use_shared_postgres`` is enabled and we're running under Home Assistant,
discover the ``shared_postgres`` add-on, provision myMeal's OWN database, and
persist the resulting DSN to ``<data_dir>/.database_url``. ``settings.sqlalchemy_uri``
reads that file (gated on the same flag), so the app comes up on Postgres.

Best-effort: any failure logs to stderr and leaves myMeal on SQLite — this must
never block startup. Run once from the entrypoint, before gunicorn:
``python3 -m app.pg_provision``.

Token bootstrap: prefer the operator-set ``postgres_provision_token``; otherwise
try to read it from the Supervisor discovery message the add-on publishes (works
only if the platform exposes it to sibling add-ons). If neither yields a token,
we log a clear instruction and stay on SQLite.
"""
import json
import os
import sys
import urllib.request

DSN_FILENAME = ".database_url"
API_PORT = 8087
APP_NAME = "mymeal"
# Only accept the driver myMeal supports. A malformed/foreign DSN written here
# would brick every subsequent boot at create_app, so reject it and stay on
# SQLite rather than persist something we can't load.
DSN_PREFIX = "postgresql+psycopg://"


def _log(message: str) -> None:
    print(f"myMeal: pg_provision: {message}", file=sys.stderr)


def _discovery_config():
    """The shared_postgres discovery message config ({host, port, provision_url,
    token}), if the Supervisor exposes the discovery list to us. Best-effort."""
    from app.services.ai.discovery import _supervisor_get

    data = _supervisor_get("/discovery")
    messages = data.get("discovery", []) if isinstance(data, dict) else (data or [])
    for msg in messages:
        if isinstance(msg, dict) and msg.get("service") == "shared_postgres":
            return msg.get("config") or {}
    return None


def _candidate_provision_urls(cfg):
    """Provisioning-API URLs to try, most-specific first: the discovered
    provision_url, then hostnames from the Supervisor add-on list, then fixed
    internal-DNS fallbacks (mirrors the Edibl discovery)."""
    from app.services.ai.discovery import _supervisor_addons, _supervisor_get

    urls, seen = [], set()

    def add(url):
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    if cfg and cfg.get("provision_url"):
        add(cfg["provision_url"])
    for addon in _supervisor_addons():
        slug, name = str(addon.get("slug", "")), str(addon.get("name", ""))
        if "postgres" not in f"{name} {slug}".lower():
            continue
        info = _supervisor_get(f"/addons/{slug}/info") or {}
        host = info.get("hostname") or addon.get("hostname")
        if host:
            add(f"http://{host}:{API_PORT}/provision")
    for host in ("local-shared-postgres", "local-shared_postgres", "shared-postgres"):
        add(f"http://{host}:{API_PORT}/provision")
    return urls


def _provision(url: str, token: str):
    payload = json.dumps({"app": APP_NAME}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read() or b"{}").get("dsn")


def main() -> int:
    from app.settings import load_settings

    settings = load_settings()
    if settings.DATABASE_URL:
        return 0  # explicit URL wins; nothing to provision
    if not settings.USE_SHARED_POSTGRES:
        return 0

    dsn_path = os.path.join(settings.data_dir, DSN_FILENAME)
    if os.path.isfile(dsn_path):
        with open(dsn_path) as fh:
            if fh.read().strip():
                return 0  # already provisioned — stable across restarts

    if not os.environ.get("SUPERVISOR_TOKEN"):
        _log("use_shared_postgres is set but not running under Home Assistant; staying on SQLite")
        return 0

    cfg = _discovery_config()
    token = (settings.POSTGRES_PROVISION_TOKEN or (cfg or {}).get("token") or "").strip()
    if not token:
        _log("no provisioning token available — set 'postgres_provision_token' to the "
             "Shared PostgreSQL add-on's token (Settings shows it). Staying on SQLite")
        return 0

    # Candidates are ordered most-trusted first (discovery message, then
    # Supervisor-/addons-identified hosts, then fixed add-on DNS names). HA
    # add-ons share a semi-trusted internal network; the DSN-scheme check below
    # is the backstop against a bad/foreign response being persisted.
    for url in _candidate_provision_urls(cfg):
        try:
            dsn = _provision(url, token)
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            _log(f"provision via {url} failed: {exc}")
            continue
        if not dsn:
            continue
        if not dsn.startswith(DSN_PREFIX):
            _log(f"ignoring provision response with unsupported DSN scheme from {url}")
            continue
        fd = os.open(dsn_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(dsn)
        _log("provisioned shared PostgreSQL; using it")
        return 0

    _log("Shared PostgreSQL add-on not reachable; staying on SQLite (will retry next start)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
