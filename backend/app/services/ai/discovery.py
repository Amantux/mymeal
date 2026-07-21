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


def _supervisor_addons() -> list[dict]:
    """Raw add-on list from the Supervisor (empty outside HA / on error)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return []
    try:
        r = httpx.get("http://supervisor/addons",
                      headers={"Authorization": f"Bearer {token}"}, timeout=PROBE_TIMEOUT)
        r.raise_for_status()
        return (r.json().get("data") or {}).get("addons") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("supervisor add-on query failed: %s", exc)
        return []


def discover_edibl() -> dict | None:
    """Locate a companion Edibl add-on/instance.

    Prefers the Supervisor add-on list (so it works with no manual URL); the
    Edibl add-on is reachable from myMeal by its container hostname on the
    internal network. Falls back to probing the usual addresses. Confirms a
    candidate by hitting Edibl's health endpoint. Returns
    {"url": ..., "via": "supervisor"|"probe"} or None. Never raises.
    """
    candidates: list[tuple[str, str]] = []
    for addon in _supervisor_addons():
        slug = str(addon.get("slug", ""))
        name = str(addon.get("name", ""))
        if "edibl" not in f"{slug} {name}".lower():
            continue
        hostname = addon.get("hostname") or slug.replace("_", "-")
        # Edibl's app port (its add-on ingress port); default 8099 per its config.
        for port in (8099, 8080, 7860):
            candidates.append((f"http://{hostname}:{port}", "supervisor"))
        if addon.get("ip_address"):
            candidates.append((f"http://{addon['ip_address']}:8099", "supervisor"))

    for host in ("http://edibl:8099", "http://homeassistant.local:8099",
                 "http://localhost:8099"):
        candidates.append((host, "probe"))

    for url, via in candidates:
        try:
            r = httpx.get(f"{url.rstrip('/')}/api/v1/misc/health", timeout=PROBE_TIMEOUT)
            if r.status_code < 500:  # reachable (200, or 401/403 if it wants auth)
                logger.info("Discovered Edibl at %s (via %s)", url, via)
                return {"url": url.rstrip("/"), "via": via,
                        "needsAuth": r.status_code in (401, 403)}
        except Exception:  # noqa: BLE001 - absence is normal
            continue
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
