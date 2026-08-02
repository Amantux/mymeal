"""Register myMeal with the Home Assistant Supervisor discovery service.

When running as a Home Assistant add-on, the Supervisor injects
``SUPERVISOR_TOKEN``. Posting to ``/discovery`` makes Home Assistant offer the
companion myMeal integration for one-click setup ("New device found").

Safe to run outside HA — it no-ops when the token is absent.
"""
import json
import os
import socket
import sys
import urllib.request

SUPERVISOR_API = "http://supervisor"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
PORT = int(os.environ.get("MYMEAL_PORT", "7850"))


def _addon_hostname(default: str) -> str:
    """The DNS name other containers resolve is the Supervisor-assigned add-on
    hostname (``local-<slug>`` / ``<repo>-<slug>``), which the Supervisor knows
    but the container itself does not. Ask for it; fall back to ``default``."""
    try:
        req = urllib.request.Request(
            f"{SUPERVISOR_API}/addons/self/info",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read() or b"{}").get("data", {})
            return data.get("hostname") or default
    except Exception as exc:  # noqa: BLE001 - best effort; fall back to default
        print(f"myMeal: could not read add-on hostname from Supervisor ({exc}).")
        return default


def mcp_config(host: str, settings=None) -> dict:
    """MCP endpoint details for the discovery payload; ``{}`` when MCP is off.

    Home Assistant's MCP Client otherwise has no way to learn where the SSE
    endpoint lives, so wiring Assist to myMeal means hand-typing a URL. Publishing
    it here means the add-on hostname and port are already known.

    Best-effort like the rest of discovery: if settings can't be read we publish
    the REST details alone rather than failing startup. Nothing is published when
    MCP is disabled — a dead URL is worse than no URL.
    """
    if settings is None:
        try:
            from app.settings import load_settings

            settings = load_settings()
        except Exception as exc:  # noqa: BLE001 - discovery must never block startup
            print(f"myMeal: MCP details omitted from discovery ({exc}).")
            return {}
    values = getattr(settings, "values", {})
    if not values.get("MCP_ENABLED"):
        return {}
    port = int(values.get("MCP_PORT") or 7851)
    config = {"mcp_port": port, "mcp_url": f"http://{host}:{port}/sse"}
    # The bearer Home Assistant must present, when one is set. Same precedent as
    # the REST `token` above: the Supervisor discovery message is the channel the
    # add-on already uses to hand HA its credential.
    server_token = str(values.get("MCP_SERVER_TOKEN") or "")
    if server_token:
        config["mcp_token"] = server_token
    return config


def build_payload(host: str, token: str, mcp: dict | None = None) -> dict:
    """The Supervisor discovery message. Existing keys are unchanged — the
    companion integration reads host/port/token — MCP details are additive."""
    config = {"host": host, "port": PORT, "token": token}
    config.update(mcp or {})
    return {"service": "mymeal", "config": config}


def main() -> int:
    if not TOKEN:
        print("SUPERVISOR_TOKEN not set — skipping HA discovery (fine outside HA).")
        return 0

    # Other containers (HA Core) reach an add-on by its Supervisor-assigned DNS
    # name (e.g. "local-mymeal") — NOT this container's own HOSTNAME, which is a
    # Docker container id and is not resolvable. Ask the Supervisor for it.
    host = _addon_hostname(os.environ.get("HOSTNAME") or socket.gethostname())

    # The integration polls the REST API directly (no ingress), so hand it a
    # long-lived API key. Best-effort: an empty token just means the integration
    # falls back to the open path (unchanged behaviour).
    token = ""
    try:
        from app.integration_token import ensure_integration_token

        token = ensure_integration_token() or ""
    except Exception as exc:  # noqa: BLE001 - discovery must never block startup
        print(f"myMeal: integration token provisioning skipped ({exc}).")

    payload = json.dumps(build_payload(host, token, mcp_config(host))).encode()

    req = urllib.request.Request(
        f"{SUPERVISOR_API}/discovery",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read() or b"{}")
            print("myMeal discovery registered:", body.get("data", {}).get("uuid"))
        return 0
    except Exception as exc:  # noqa: BLE001 - best effort, never block startup
        print(f"Discovery registration failed (non-fatal): {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
