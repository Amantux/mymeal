"""Status / health endpoints."""
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
    return jsonify({"ok": True})


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
