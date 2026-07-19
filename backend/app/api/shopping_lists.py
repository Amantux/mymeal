from datetime import date

from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import ShoppingList, ShoppingListItem, Recipe
from ..auth import login_required, current_group
from ..schemas.serializers import shopping_list_out, shopping_item_out
from ..services.shopping import build_from_recipes
from ..utils import to_float
from .mealplans import recipe_ids_in_range

bp = Blueprint("shopping_lists", __name__)


def _get_list(list_id) -> ShoppingList:
    sl = db.session.get(ShoppingList, list_id)
    if not sl or sl.group_id != current_group().id:
        abort(404)
    return sl


def _get_item(item_id) -> ShoppingListItem:
    item = db.session.get(ShoppingListItem, item_id)
    if not item or item.shopping_list.group_id != current_group().id:
        abort(404)
    return item


def _parse_date(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


@bp.get("/shopping-lists")
@login_required
def list_lists():
    lists = (
        db.session.query(ShoppingList)
        .filter_by(group_id=current_group().id)
        .order_by(ShoppingList.created_at.asc())
        .all()
    )
    return jsonify({"items": [shopping_list_out(sl) for sl in lists]})


@bp.post("/shopping-lists")
@login_required
def create_list():
    data = request.get_json(silent=True) or {}
    sl = ShoppingList(
        name=data.get("name") or "Shopping List", group_id=current_group().id
    )
    db.session.add(sl)
    db.session.commit()
    return jsonify(shopping_list_out(sl)), 201


@bp.get("/shopping-lists/<list_id>")
@login_required
def get_list(list_id):
    return jsonify(shopping_list_out(_get_list(list_id)))


@bp.delete("/shopping-lists/<list_id>")
@login_required
def delete_list(list_id):
    db.session.delete(_get_list(list_id))
    db.session.commit()
    return "", 204


def _next_position(sl) -> int:
    return (max((i.position for i in sl.items), default=-1)) + 1


@bp.post("/shopping-lists/<list_id>/items")
@login_required
def add_item(list_id):
    sl = _get_list(list_id)
    data = request.get_json(force=True) or {}
    item = ShoppingListItem(
        display=data.get("display", ""),
        quantity=to_float(data.get("quantity")),
        unit=data.get("unit", ""),
        aisle=data.get("aisle", ""),
        position=_next_position(sl),
        shopping_list_id=sl.id,
    )
    db.session.add(item)
    db.session.commit()
    return jsonify(shopping_item_out(item)), 201


@bp.put("/shopping-lists/items/<item_id>")
@login_required
def update_item(item_id):
    item = _get_item(item_id)
    data = request.get_json(force=True) or {}
    if "display" in data:
        item.display = data["display"]
    if "quantity" in data:
        item.quantity = to_float(data["quantity"])
    if "unit" in data:
        item.unit = data["unit"]
    if "aisle" in data:
        item.aisle = data["aisle"]
    if "checked" in data:
        item.checked = bool(data["checked"])
    db.session.commit()
    return jsonify(shopping_item_out(item))


@bp.delete("/shopping-lists/items/<item_id>")
@login_required
def delete_item(item_id):
    db.session.delete(_get_item(item_id))
    db.session.commit()
    return "", 204


def _append_consolidated(sl, recipes):
    base = _next_position(sl)
    built = build_from_recipes(recipes)
    for row in built:
        db.session.add(
            ShoppingListItem(
                display=row["display"],
                quantity=row["quantity"],
                unit=row["unit"],
                aisle=row["aisle"],
                position=base + row["position"],
                food_id=row["foodId"],
                shopping_list_id=sl.id,
            )
        )
    db.session.commit()
    return len(built)


@bp.post("/shopping-lists/<list_id>/from-recipes")
@login_required
def from_recipes(list_id):
    sl = _get_list(list_id)
    data = request.get_json(force=True) or {}
    ids = data.get("recipeIds") or []
    recipes = (
        db.session.query(Recipe)
        .filter(Recipe.id.in_(ids), Recipe.group_id == current_group().id)
        .all()
    )
    count = _append_consolidated(sl, recipes)
    return jsonify({**shopping_list_out(sl), "added": count}), 201


@bp.post("/shopping-lists/<list_id>/from-mealplan")
@login_required
def from_mealplan(list_id):
    sl = _get_list(list_id)
    data = request.get_json(force=True) or {}
    gid = current_group().id
    ids = recipe_ids_in_range(
        gid, _parse_date(data.get("start")), _parse_date(data.get("end"))
    )
    recipes = (
        db.session.query(Recipe)
        .filter(Recipe.id.in_(ids), Recipe.group_id == gid)
        .all()
    )
    count = _append_consolidated(sl, recipes)
    return jsonify({**shopping_list_out(sl), "added": count}), 201
