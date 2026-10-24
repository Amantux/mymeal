"""Household food preferences — read/update the diet, allergies, dislikes and
notes the AI honours. Group-scoped; any member may view and adjust them (they
are shared household settings, not secrets)."""
from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..auth import login_required, current_group
from ..services.preferences import (
    FIELDS,
    MEASURE_BIAS_VALUES,
    get_preferences,
    measure_bias,
    set_measure_bias,
    set_preferences,
)

bp = Blueprint("preferences", __name__)


@bp.get("/preferences")
@login_required
def get_prefs():
    gid = current_group().id
    out = get_preferences(gid)
    # Shipped alongside the text preferences rather than on its own route:
    # it is the same screen and the same scope, and a second round-trip for
    # one enum is not worth a second endpoint.
    out["measureBias"] = measure_bias(gid)
    out["measureBiasOptions"] = list(MEASURE_BIAS_VALUES)
    return jsonify(out)


@bp.put("/preferences")
@login_required
def put_prefs():
    data = request.get_json(silent=True) or {}
    gid = current_group().id
    payload = {f: data[f] for f in FIELDS if f in data}
    result = set_preferences(gid, payload)
    if "measureBias" in data:
        try:
            set_measure_bias(gid, str(data.get("measureBias") or ""))
        except ValueError as exc:
            # Typed, curated message — the enum is small enough to name, and
            # naming it is what lets a client fix the call.
            db.session.rollback()
            abort(400, description=str(exc))
    result["measureBias"] = measure_bias(gid)
    result["measureBiasOptions"] = list(MEASURE_BIAS_VALUES)
    db.session.commit()
    return jsonify(result)
