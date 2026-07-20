"""Status / health endpoints."""
import os

from flask import Blueprint, current_app, jsonify

bp = Blueprint("misc", __name__)


@bp.get("/status")
def status():
    return jsonify(
        {
            "health": True,
            "versions": ["v1"],
            "title": "myMeal",
            "message": "myMeal — recipes, planning & AI cooking assistant",
        }
    )


@bp.get("/misc/health")
def health():
    """Kept for backward compatibility with existing Docker health checks."""
    return jsonify({"ok": True})


@bp.get("/health/live")
def health_live():
    """Liveness: the process is up and serving. Deliberately checks NOTHING
    else — a liveness probe that fails on a dependency outage causes an
    orchestrator to restart a healthy app, turning degradation into downtime."""
    return jsonify({"status": "alive"}), 200


@bp.get("/health/ready")
def health_ready():
    """Readiness: can this instance actually serve requests right now?

    Only hard dependencies count. An unreachable AI provider is reported for
    visibility but does NOT make the app unready — AI is optional, and letting
    it gate readiness would take the whole recipe manager offline because a
    third party had an outage.
    """
    from sqlalchemy import text

    from ..extensions import db

    settings = current_app.config.get("SETTINGS")
    checks: dict[str, str] = {}

    try:
        db.session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report, never leak the URL
        current_app.logger.warning("readiness: database check failed: %s", exc)
        checks["database"] = "error"

    try:
        images = current_app.config["images_dir"]()
        probe = os.path.join(images, ".readiness")
        with open(probe, "w") as fh:
            fh.write("")
        os.unlink(probe)
        checks["storage"] = "ok"
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("readiness: storage check failed: %s", exc)
        checks["storage"] = "error"

    required = ["database", "storage"]

    # MCP counts only when the operator declared it required.
    if settings is not None and settings.MCP_ENABLED:
        alive = _mcp_alive(settings)
        checks["mcp"] = "ok" if alive else "error"
        if settings.MCP_REQUIRED:
            required.append("mcp")

    # Optional, informational only.
    if settings is not None and settings.ai_enabled:
        checks["ai_provider"] = "configured"

    not_ready = [name for name in required if checks.get(name) != "ok"]
    body = {
        "status": "ready" if not not_ready else "not ready",
        "required": required,
        "notReady": not_ready,
        "checks": checks,
    }
    return jsonify(body), (200 if not not_ready else 503)


def _mcp_alive(settings) -> bool:
    """Cheap TCP probe. Bounded so a hung MCP cannot hang the health check."""
    import socket

    host = "127.0.0.1" if settings.MCP_HOST in ("0.0.0.0", "") else settings.MCP_HOST
    try:
        with socket.create_connection((host, settings.MCP_PORT), timeout=1):
            return True
    except OSError:
        return False


@bp.get("/misc/auth-mode")
def auth_mode():
    """Unauthenticated: tells the SPA whether sign-in is required at all.

    This exists so the frontend never has to *infer* the auth mode from whether
    an unauthenticated ``/users/self`` happened to succeed. Behind Home Assistant
    ingress the add-on runs with ``disable_auth: true``, and a transient error on
    that inference would bounce the user to a login screen that is meaningless
    there (HA has already authenticated them at the ingress layer).

    Discloses nothing an anonymous caller could not already determine by calling
    ``/users/self`` without a token.
    """
    disabled = bool(current_app.config["DISABLE_AUTH"])
    return jsonify({
        "authDisabled": disabled,
        "loginRequired": not disabled,
        "allowRegistration": bool(current_app.config.get("ALLOW_REGISTRATION")),
    })
