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


def _edibl_candidates() -> list[tuple[str, str]]:
    """(base_url, via) candidates for an Edibl instance, best first.

    Asks the Supervisor for installed add-ons whose slug/name mentions Edibl,
    then fetches each one's info for the real container hostname + ingress port
    (the add-on is reachable at that hostname:port on the internal network).
    """
    out: list[tuple[str, str]] = []
    for addon in _supervisor_addons():
        slug = str(addon.get("slug", ""))
        name = str(addon.get("name", ""))
        if "edibl" not in f"{slug} {name}".lower():
            continue
        info = _supervisor_get(f"/addons/{slug}/info") or {}
        hostname = info.get("hostname") or addon.get("hostname") or slug.replace("_", "-")
        port = info.get("ingress_port") or EDIBL_DEFAULT_PORT
        out.append((f"http://{hostname}:{port}", "supervisor"))
        if info.get("ip_address"):
            out.append((f"http://{info['ip_address']}:{port}", "supervisor"))
    # Fixed fallbacks (local add-on hostname, mDNS, bare service name).
    for host in ("http://local-edibl", "http://edibl", "http://homeassistant.local"):
        out.append((f"{host}:{EDIBL_DEFAULT_PORT}", "probe"))
    return out


def discover_edibl() -> dict | None:
    """Locate a companion Edibl add-on/instance. Confirms a candidate by hitting
    Edibl's status endpoint. Returns {"url", "via", "needsAuth"} or None. Never
    raises. (Requires hassio_api for the Supervisor path; the fixed fallbacks
    work without it if the hostnames happen to match.)"""
    for url, via in _edibl_candidates():
        base = url.rstrip("/")
        try:
            r = httpx.get(f"{base}{EDIBL_HEALTH_PATH}", timeout=PROBE_TIMEOUT)
        except Exception:  # noqa: BLE001 - absence is the normal case
            continue
        if r.status_code < 500:  # answered (200, or 401/403 if it wants auth)
            logger.info("Discovered Edibl at %s (via %s)", base, via)
            return {"url": base, "via": via, "needsAuth": r.status_code in (401, 403)}
    return None


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
