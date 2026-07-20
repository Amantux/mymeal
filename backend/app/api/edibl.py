"""myMeal → Edibl control endpoints (the companion food-inventory app).

These let myMeal (and its UI) drive the integration:
  * GET  /edibl/status     — is Edibl configured and reachable?
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


@bp.get("/edibl/status")
@login_required
def status():
    return jsonify(EdiblClient.from_settings().status())


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
