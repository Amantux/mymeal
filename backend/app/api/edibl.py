"""myMeal → Edibl control endpoints (the companion food-inventory app).

These let myMeal (and its UI) drive the integration:
  * GET  /edibl/config     — current connection config (URL + tokenSet), redacted
  * PUT  /edibl/config     — set URL / token (remembered; overrides env/add-on)
  * GET  /edibl/discover   — auto-find a companion Edibl add-on via Supervisor
  * GET  /edibl/status     — is Edibl configured and reachable? (test connection)
  * GET  /edibl/stock      — pull real inventory from Edibl (pantry-aware cooking)
  * POST /edibl/push-plan  — push upcoming plan ingredients to Edibl

Every call degrades gracefully: an unconfigured or unreachable Edibl returns a
clear status, never a 500. The reciprocal direction (Edibl pulling FROM myMeal)
is served by GET /api/v1/plan/ingredients in the mealplans blueprint.
"""
from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, request

from ..auth import login_required, current_group
from ..extensions import db
from ..models import MealPlanEntry
from ..services.edibl import EdiblClient
from ..services.plan_ingredients import flatten_plan, upcoming_window
from ..utils import to_int

bp = Blueprint("edibl", __name__)


def _base_settings():
    from flask import current_app
    return current_app.config["SETTINGS"]


@bp.get("/edibl/config")
@login_required
def get_config():
    """Redacted connection config for the UI. Never returns the token."""
    from ..services.edibl_config import config_view

    return jsonify(config_view(_base_settings(), current_group().id))


@bp.put("/edibl/config")
@login_required
def put_config():
    """Persist the Edibl connection (overrides env/add-on, remembered).
    Body: {url?, token?, clearToken?}. Blank/omitted token keeps the stored one."""
    import re

    from ..services.edibl_config import config_view, set_config

    data = request.get_json(silent=True) or {}
    url = data.get("url")
    if url and not re.match(r"^https?://", str(url).strip()):
        return jsonify({"error": "url must start with http:// or https://"}), 422

    kwargs = {}
    if "url" in data:
        kwargs["url"] = str(data["url"] or "")
    if data.get("clearToken"):
        kwargs["clear_token"] = True
    elif data.get("token"):
        kwargs["token"] = str(data["token"])

    set_config(current_group().id, **kwargs)
    return jsonify(config_view(_base_settings(), current_group().id))


@bp.get("/edibl/discover")
@login_required
def discover():
    """Auto-find a companion Edibl add-on (Supervisor) so the user needn't type
    a URL. In the Home Assistant add-on case both apps sit behind ingress, so no
    token is needed — discovering the URL is enough."""
    from ..services.ai.discovery import discover_edibl

    found = discover_edibl()
    if not found:
        return jsonify({
            "found": False,
            "hint": "No Edibl add-on found. Install the Edibl add-on, or enter "
                    "its URL manually.",
        })
    return jsonify({"found": True, **found})


@bp.get("/edibl/status")
@login_required
def status():
    """Connection status / test. An optional ?url= probes that URL WITHOUT
    persisting it (reusing the saved token), so 'Test connection' on a typed-but-
    unsaved URL never mutates stored config."""
    client = EdiblClient.from_settings()
    probe_url = request.args.get("url")
    if probe_url:
        client = EdiblClient(probe_url.strip().rstrip("/"), client.token,
                             timeout=client.timeout)
    return jsonify(client.status())


@bp.get("/edibl/stock")
@login_required
def stock():
    """Real inventory from Edibl, normalised to {name, quantity, unit}."""
    result = EdiblClient.from_settings().get_stock()
    if not result.get("configured"):
        return jsonify({"configured": False, "items": []})
    # `ok`, not `reachable`: a 401/500 answered but gave no usable stock.
    if not result.get("ok"):
        return jsonify({"configured": True, "reachable": result.get("reachable", False),
                        "error": result.get("error"), "items": []}), 502
    return jsonify(result)


@bp.post("/edibl/push-plan")
@login_required
def push_plan():
    """Push the upcoming plan's ingredients to Edibl for stock reconciliation.

    Body (optional): {days?: int}. Returns Edibl's upsert result, or a clear
    not-configured/unreachable status.
    """
    client = EdiblClient.from_settings()
    if not client.configured:
        return jsonify({"configured": False,
                        "error": "Edibl not configured (set MYMEAL_EDIBL_URL)"}), 400

    data = request.get_json(silent=True) or {}
    days = to_int(data.get("days"), 7)
    start, end = upcoming_window(date.today(), days)
    entries = (
        db.session.query(MealPlanEntry)
        .filter(MealPlanEntry.group_id == current_group().id)
        .filter(MealPlanEntry.date >= start, MealPlanEntry.date <= end)
        .order_by(MealPlanEntry.date.asc())
        .all()
    )
    items = flatten_plan(entries)
    if not items:
        return jsonify({"pushed": 0, "reason": "no planned ingredients in window"})

    result = client.push_plan(items, source="mymeal")
    # `ok`, not `reachable`: a 401/500 must not report a successful push.
    if not result.get("ok"):
        return jsonify({"configured": True, "reachable": result.get("reachable", False),
                        "error": result.get("error")}), 502
    return jsonify({"pushed": len(items), "edibl": result.get("data")})
