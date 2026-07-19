"""Foods (canonical ingredients) and units of measure."""
from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import Food, Unit
from ..auth import login_required, current_group
from ..schemas.serializers import food_out, unit_out

bp = Blueprint("foods", __name__)


# --- Foods ---------------------------------------------------------------
def _get_food(food_id):
    food = db.session.get(Food, food_id)
    if not food or food.group_id != current_group().id:
        abort(404)
    return food


def _aliases_str(value):
    if isinstance(value, list):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return value or ""


@bp.get("/foods")
@login_required
def list_foods():
    foods = (
        db.session.query(Food)
        .filter_by(group_id=current_group().id)
        .order_by(Food.name.asc())
        .all()
    )
    return jsonify([food_out(f) for f in foods])


@bp.post("/foods")
@login_required
def create_food():
    data = request.get_json(force=True) or {}
    food = Food(
        name=data.get("name", ""),
        plural_name=data.get("pluralName", ""),
        aliases=_aliases_str(data.get("aliases")),
        aisle=data.get("aisle", ""),
        description=data.get("description", ""),
        group_id=current_group().id,
    )
    db.session.add(food)
    db.session.commit()
    return jsonify(food_out(food)), 201


@bp.put("/foods/<food_id>")
@login_required
def update_food(food_id):
    food = _get_food(food_id)
    data = request.get_json(force=True) or {}
    if "name" in data:
        food.name = data["name"]
    if "pluralName" in data:
        food.plural_name = data["pluralName"]
    if "aliases" in data:
        food.aliases = _aliases_str(data["aliases"])
    if "aisle" in data:
        food.aisle = data["aisle"]
    if "description" in data:
        food.description = data["description"]
    db.session.commit()
    return jsonify(food_out(food))


@bp.delete("/foods/<food_id>")
@login_required
def delete_food(food_id):
    db.session.delete(_get_food(food_id))
    db.session.commit()
    return "", 204


# --- Units ---------------------------------------------------------------
def _get_unit(unit_id):
    unit = db.session.get(Unit, unit_id)
    if not unit or unit.group_id != current_group().id:
        abort(404)
    return unit


@bp.get("/units")
@login_required
def list_units():
    units = (
        db.session.query(Unit)
        .filter_by(group_id=current_group().id)
        .order_by(Unit.name.asc())
        .all()
    )
    return jsonify([unit_out(u) for u in units])


@bp.post("/units")
@login_required
def create_unit():
    data = request.get_json(force=True) or {}
    unit = Unit(
        name=data.get("name", ""),
        plural_name=data.get("pluralName", ""),
        abbreviation=data.get("abbreviation", ""),
        group_id=current_group().id,
    )
    db.session.add(unit)
    db.session.commit()
    return jsonify(unit_out(unit)), 201


@bp.delete("/units/<unit_id>")
@login_required
def delete_unit(unit_id):
    db.session.delete(_get_unit(unit_id))
    db.session.commit()
    return "", 204
