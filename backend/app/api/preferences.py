"""Household food preferences — read/update the diet, allergies, dislikes and
notes the AI honours. Group-scoped; any member may view and adjust them (they
are shared household settings, not secrets)."""
from flask import Blueprint, request, jsonify

from ..extensions import db
from ..auth import login_required, current_group
from ..services.preferences import get_preferences, set_preferences, FIELDS

bp = Blueprint("preferences", __name__)


@bp.get("/preferences")
@login_required
def get_prefs():
    return jsonify(get_preferences(current_group().id))


@bp.put("/preferences")
@login_required
def put_prefs():
    data = request.get_json(silent=True) or {}
    payload = {f: data[f] for f in FIELDS if f in data}
    result = set_preferences(current_group().id, payload)
    db.session.commit()
    return jsonify(result)
