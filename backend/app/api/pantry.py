from datetime import date

from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import PantryItem, Food
from ..auth import login_required, current_group
from ..schemas.serializers import pantry_item_out
from ..utils import to_float

bp = Blueprint("pantry", __name__)


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _valid_food_id(food_id):
    if not food_id:
        return None
    f = db.session.get(Food, food_id)
    return f.id if f and f.group_id == current_group().id else None


def _get(item_id) -> PantryItem:
    item = db.session.get(PantryItem, item_id)
    if not item or item.group_id != current_group().id:
        abort(404)
    return item


@bp.get("/pantry")
@login_required
def list_pantry():
    items = (
        db.session.query(PantryItem)
        .filter_by(group_id=current_group().id)
        .order_by(PantryItem.label.asc())
        .all()
    )
    return jsonify({"items": [pantry_item_out(p) for p in items]})


@bp.post("/pantry")
@login_required
def create_pantry():
    data = request.get_json(force=True) or {}
    item = PantryItem(
        label=data.get("label", ""),
        quantity=to_float(data.get("quantity")),
        unit=data.get("unit", ""),
        location=data.get("location", ""),
        expires_at=_parse_date(data.get("expiresAt")),
        food_id=_valid_food_id(data.get("foodId")),
        group_id=current_group().id,
    )
    db.session.add(item)
    db.session.commit()
    return jsonify(pantry_item_out(item)), 201


@bp.put("/pantry/<item_id>")
@login_required
def update_pantry(item_id):
    item = _get(item_id)
    data = request.get_json(force=True) or {}
    if "label" in data:
        item.label = data["label"]
    if "quantity" in data:
        item.quantity = to_float(data["quantity"])
    if "unit" in data:
        item.unit = data["unit"]
    if "location" in data:
        item.location = data["location"]
    if "expiresAt" in data:
        item.expires_at = _parse_date(data["expiresAt"])
    if "foodId" in data:
        item.food_id = _valid_food_id(data["foodId"])
    db.session.commit()
    return jsonify(pantry_item_out(item))


@bp.delete("/pantry/<item_id>")
@login_required
def delete_pantry(item_id):
    db.session.delete(_get(item_id))
    db.session.commit()
    return "", 204
