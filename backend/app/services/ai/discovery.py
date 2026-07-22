"""Find a local Ollama server without making the user type a URL.

Why this exists
---------------
Home Assistant's own Ollama integration connects OUT to an Ollama server you
run yourself; it does not proxy that model to other applications. So myMeal
cannot "borrow" Home Assistant's LLM through Home Assistant.

But the useful consequence is simpler: if you already run Ollama for Home
Assistant, *the same server is reachable from myMeal directly*. The only real
friction is knowing its address. This module removes that friction by asking
the Supervisor what add-ons are installed and, failing that, probing the
handful of addresses an Ollama server realistically lives at.

Everything here is best-effort, bounded, and never raises: discovery is a
convenience, and a slow or absent Ollama must never delay startup or a request.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Short: these are all LAN/loopback addresses. A long timeout here would mean a
# user without Ollama waits on every probe.
PROBE_TIMEOUT = 1.5

# Addresses worth trying, in order of how likely they are to be right.
#   - the add-on network name, when myMeal runs as an HA add-on alongside one
#   - the Docker bridge gateway, i.e. Ollama on the Docker host
#   - Docker Desktop's host alias
#   - the HA host itself by mDNS name
#   - plain localhost, for bare-metal installs
CANDIDATE_HOSTS = (
    "http://homeassistant.local:11434",
    "http://host.docker.internal:11434",
    "http://172.17.0.1:11434",
    "http://localhost:11434",
)


def _probe(base_url: str, timeout: float = PROBE_TIMEOUT) -> list[str] | None:
    """Return the model list if an Ollama server answers here, else None."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception:  # noqa: BLE001 - absence is the normal case, not an error
        return None
    if not isinstance(data, dict) or "models" not in data:
        return None
    return [m.get("name", "") for m in data.get("models") or [] if isinstance(m, dict)]


def _supervisor_addon_hosts() -> list[str]:
    """Ask the Supervisor for installed add-ons that look like Ollama.

    Only possible inside Home Assistant, where SUPERVISOR_TOKEN is injected.
    Returns candidate base URLs, most specific first.
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return []
    try:
        r = httpx.get(
            "http://supervisor/addons",
            headers={"Authorization": f"Bearer {token}"},
            timeout=PROBE_TIMEOUT,
        )
        r.raise_for_status()
        addons = (r.json().get("data") or {}).get("addons") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("ollama discovery: supervisor query failed: %s", exc)
        return []

    hosts: list[str] = []
    for addon in addons:
        slug = str(addon.get("slug", ""))
        name = str(addon.get("name", ""))
        if "ollama" not in f"{slug} {name}".lower():
            continue
        # Add-ons are reachable from one another by their hostname on the
        # internal Docker network.
        hostname = addon.get("hostname") or slug.replace("_", "-")
        hosts.append(f"http://{hostname}:11434")
        if addon.get("ip_address"):
            hosts.append(f"http://{addon['ip_address']}:11434")
    return hosts


# Edibl's REST API listens on its add-on ingress port; this is the default in
# Edibl's add-on config. Used only as a fallback when the Supervisor doesn't
# report the port.
EDIBL_DEFAULT_PORT = 7746
# Edibl's health/status path (its api is under /api/v1, misc exposes /status).
EDIBL_HEALTH_PATH = "/api/v1/status"


def _supervisor_get(path: str):
    """GET a Supervisor endpoint with the add-on token. Returns parsed JSON
    ``data`` or None. Requires ``hassio_api: true`` in the add-on config."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        r = httpx.get(f"http://supervisor{path}",
                      headers={"Authorization": f"Bearer {token}"}, timeout=PROBE_TIMEOUT)
        r.raise_for_status()
        return r.json().get("data")
    except Exception as exc:  # noqa: BLE001
        logger.debug("supervisor GET %s failed: %s", path, exc)
        return None


def _supervisor_addons() -> list[dict]:
    data = _supervisor_get("/addons")
    return (data or {}).get("addons") or []


def _edibl_internal_port(info: dict) -> int:
    """Edibl's internal port: prefer ingress_port, else the first mapped
    container port, else the default."""
    if info.get("ingress_port"):
        return info["ingress_port"]
    for container_port in (info.get("network") or {}):
        try:
            return int(str(container_port).split("/")[0])
        except (ValueError, TypeError):
            continue
    return EDIBL_DEFAULT_PORT


def _probe_edibl(url: str) -> dict:
    """Probe a candidate URL. reachable = any status < 500 (a 401/403 still means
    'something is there', just wants auth)."""
    try:
        r = httpx.get(f"{url.rstrip('/')}{EDIBL_HEALTH_PATH}", timeout=PROBE_TIMEOUT)
        return {"reachable": r.status_code < 500, "status": r.status_code, "error": None}
    except Exception as exc:  # noqa: BLE001 - absence is the normal case
        return {"reachable": False, "status": None, "error": type(exc).__name__}


def _edibl_candidate_hosts() -> list[tuple[str, str, bool | None]]:
    """(url, slug, running) candidates to try, deduped. Combines the Supervisor
    add-on list (when permitted) with fixed internal hostnames, so discovery
    works even when the Supervisor denies reading a *sibling* add-on's info
    (which needs the manager role). Reachability is NOT checked here.

    Copied from Edibl's discover_mymeal so both apps find each other the same
    way, incl. the `local-<slug>` / dashed-slug hostname variants.
    """
    seen: set[str] = set()
    out: list[tuple[str, str, bool | None]] = []

    def add(host: str, port: int, slug: str = "", running: bool | None = None):
        if not host:
            return
        url = f"http://{host}:{port}"
        if url in seen:
            return
        seen.add(url)
        out.append((url, slug, running))

    for addon in _supervisor_addons():
        slug = str(addon.get("slug", ""))
        name = str(addon.get("name", ""))
        if "edibl" not in f"{name} {slug}".lower():
            continue
        # /info on a SIBLING add-on needs the manager role; tolerate its absence.
        info = _supervisor_get(f"/addons/{slug}/info") or {}
        running = addon.get("state") == "started"
        port = _edibl_internal_port(info) if info else EDIBL_DEFAULT_PORT
        add(info.get("hostname") or addon.get("hostname"), port, slug, running)
        if slug:
            add(f"local-{slug}", port, slug, running)
            add(slug.replace("_", "-"), port, slug, running)

    for host in ("local-edibl", "edibl", "homeassistant.local"):
        add(host, EDIBL_DEFAULT_PORT)
    return out


def discover_edibl() -> dict | None:
    """Find a companion Edibl on the internal add-on network. Returns the first
    candidate whose status endpoint actually answers (so it works even when the
    Supervisor denies cross-add-on queries and never returns a dead host), or
    None. Never raises. Shape kept back-compatible: {"url", "via", "needsAuth"}.
    """
    for url, slug, _running in _edibl_candidate_hosts():
        probe = _probe_edibl(url)
        if probe["reachable"]:
            logger.info("Discovered Edibl at %s", url)
            return {"url": url, "via": "supervisor" if slug else "probe",
                    "needsAuth": probe["status"] in (401, 403)}
    return None


def discover_edibl_debug() -> dict:
    """Read-only diagnostics for the 'Find Edibl' button: is the Supervisor
    token present, was the add-on list readable (and if not, is it a
    missing-manager-role problem), which add-ons matched, and every host tried
    with its probe result. Contains no secrets. Copied from Edibl."""
    token = bool(os.environ.get("SUPERVISOR_TOKEN"))
    # /addons/self/info is readable by the DEFAULT role, so it isolates the
    # failure mode: self works but /addons doesn't => needs manager role.
    self_info = _supervisor_get("/addons/self/info") or {}
    addons = _supervisor_get("/addons")
    if not token:
        addons_state = "no-supervisor-token"
    elif addons is None:
        addons_state = "denied-need-manager-role" if self_info else "denied-or-unreachable"
    else:
        addons_state = "ok"
    matched = [{"slug": a.get("slug"), "name": a.get("name"), "state": a.get("state")}
               for a in (addons or {}).get("addons", [])
               if "edibl" in f"{a.get('name', '')} {a.get('slug', '')}".lower()]
    tried = [{"url": url, **_probe_edibl(url)} for url, _s, _r in _edibl_candidate_hosts()]
    return {"supervisorToken": token, "supervisorAddonsQuery": addons_state,
            "selfHostname": self_info.get("hostname"), "matchedAddons": matched,
            "tried": tried, "found": [t["url"] for t in tried if t["reachable"]]}


def discover_ollama() -> dict | None:
    """Locate a reachable Ollama server.

    Returns ``{"host": url, "models": [...], "via": "supervisor"|"probe"}`` or
    None. Never raises.
    """
    for host in _supervisor_addon_hosts():
        models = _probe(host)
        if models is not None:
            logger.info("Discovered Ollama add-on at %s (%d models)", host, len(models))
            return {"host": host, "models": models, "via": "supervisor"}

    for host in CANDIDATE_HOSTS:
        models = _probe(host)
        if models is not None:
            logger.info("Discovered Ollama at %s (%d models)", host, len(models))
            return {"host": host, "models": models, "via": "probe"}

    return None
