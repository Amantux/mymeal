"""Learned unit→weight conversions: see them, accept them, forget them.

Every number this app looked up on the internet is listed here with its source,
so a household can check the app's working. That is the point of the screen: a
conversion nobody can inspect is a conversion nobody should trust.
"""
from flask import Blueprint, request, jsonify, abort

from ..auth import login_required, current_group
from ..extensions import db
from ..models.unit_conversion import UnitConversion

bp = Blueprint("conversions", __name__)


def _out(row):
    return {
        "id": row.id,
        "unit": row.unit,
        "foodTerm": row.food_term,
        "gramsPerUnit": row.grams_per_unit,
        "source": row.source,
        "sourceUrl": row.source_url,
        "confidence": row.confidence,
        "status": row.status,
    }


def _get(conversion_id):
    row = db.session.get(UnitConversion, conversion_id)
    # Scope check, not just a primary-key lookup: a bare get() by id is how one
    # household ends up editing another's data.
    if not row or row.group_id != current_group().id:
        abort(404)
    return row


@bp.get("/conversions")
@login_required
def list_conversions():
    rows = (
        db.session.query(UnitConversion)
        .filter_by(group_id=current_group().id)
        # Pending first: the ones needing a decision are the reason to open this.
        .order_by(UnitConversion.status.desc(), UnitConversion.food_term.asc(),
                  UnitConversion.unit.asc())
        .all()
    )
    return jsonify([_out(r) for r in rows])


@bp.put("/conversions/<conversion_id>")
@login_required
def update_conversion(conversion_id):
    """Accept a pending conversion, or correct its weight.

    A human-set weight becomes ``source="user"`` — it must not keep claiming the
    web said something it did not.
    """
    row = _get(conversion_id)
    data = request.get_json(force=True) or {}

    if "gramsPerUnit" in data:
        try:
            grams = float(data["gramsPerUnit"])
        except (TypeError, ValueError):
            return jsonify({"error": "the weight must be a number"}), 422
        if not 0 < grams <= 5000:
            return jsonify({"error": "that weight is outside the plausible range "
                                     "(0–5000 g per unit)"}), 422
        if grams != row.grams_per_unit:
            row.grams_per_unit = grams
            row.source = "user"
            row.source_url = ""
            row.confidence = 1.0

    if data.get("status") in ("confirmed", "pending"):
        row.status = data["status"]

    db.session.commit()
    return jsonify(_out(row))


@bp.delete("/conversions/<conversion_id>")
@login_required
def delete_conversion(conversion_id):
    """Forget a conversion. The next import that needs it looks it up again."""
    db.session.delete(_get(conversion_id))
    db.session.commit()
    return "", 204
