from datetime import date, timedelta

from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import ShoppingList, ShoppingListItem, Recipe
from ..auth import login_required, current_group
from ..schemas.serializers import shopping_list_out, shopping_item_out
from ..services.shopping import build_from_recipes
from ..utils import to_float
from .mealplans import entries_in_range

# Import parse_line for bulk item parsing
from ..services.units import parse_line

bp = Blueprint("shopping_lists", __name__)


def _shopping_load_opts():
    """Eager-load the ingredient graph a few levels deep so building a list from
    recipes with components doesn't fire a query per row. Shared with meal-plan +
    inventory via the one component expander."""
    from ..services.components import component_load_opts
    return component_load_opts()


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
    from sqlalchemy.orm import selectinload
    lists = (
        db.session.query(ShoppingList)
        .options(selectinload(ShoppingList.items))  # avoid a query per list
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


def _append_consolidated(sl, recipes, from_entries=False):
    base = _next_position(sl)
    from ..services.shopping import build_from_entries
    built = build_from_entries(recipes) if from_entries else build_from_recipes(recipes)
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
        .options(*_shopping_load_opts())
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
    # Build from ENTRIES, not a deduped recipe set: cooking a recipe on Monday
    # AND Thursday must buy its ingredients twice, and a per-entry serving
    # override must scale them. The old path collapsed duplicates via
    # Recipe.id.in_(ids) and never saw entry.servings.
    # An omitted (or unparseable) date range used to mean NO date filter at all, so
    # "build from the plan" quietly bought every meal ever planned, back years.
    # Default to the week ahead — the same window /plan/ingredients uses — and
    # refuse a date we couldn't read rather than silently widening to everything.
    for key in ("start", "end"):
        if data.get(key) and _parse_date(data[key]) is None:
            return jsonify({"error": f"unparseable {key} date"}), 422
    start = _parse_date(data.get("start")) or date.today()
    end = _parse_date(data.get("end")) or (start + timedelta(days=7))
    entries = entries_in_range(gid, start, end)
    pairs = []
    for e in entries:
        if not e.recipe:
            continue
        mult = 1.0
        if e.servings and e.recipe.servings and e.recipe.servings > 0:
            mult = e.servings / e.recipe.servings
        pairs.append((e.recipe, mult))
    count = _append_consolidated(sl, pairs, from_entries=True)
    return jsonify({**shopping_list_out(sl), "added": count}), 201


def _parse_bulk_item_line(line: str) -> dict:
    """Parse a single shopping list item line.

    Returns a dict with display, quantity (float or None), unit (str or None), aisle (str).
    Uses parse_line from units module for structured parsing, falls back to simple split.
    """
    line = (line or "").strip()
    if not line:
        return None

    parsed = parse_line(line)
    qty = parsed.get("qty")
    unit = parsed.get("unit")
    rest = parsed.get("rest", "")

    # If parse_line succeeded and extracted meaningful data, use it
    if qty is not None or unit:
        # Build display from parsed components
        # If rest is empty but we have qty/unit, use the original line as display
        if not rest:
            rest = line
        return {
            "display": rest,
            "quantity": qty,
            "unit": unit,
            "aisle": "",
        }

    # Fallback: simple leading-number+unit split
    # Try to extract a number at the start
    import re
    match = re.match(r'^(\d+(?:\.\d+)?(?:\s+\d+/\d+)?)\s*(.*)$', line, re.IGNORECASE)
    if match:
        qty_str = match.group(1)
        rest = match.group(2).strip()
        # Try to parse the quantity
        try:
            qty = float(qty_str)
        except ValueError:
            # Could be "1 1/2" format
            qty = None

        # Try to extract unit from the rest
        unit = None
        if qty is not None:
            # Check if rest starts with a known unit
            from ..services.units import canonical_unit
            rest_lower = rest.lower()
            for u in ["g", "kg", "oz", "lb", "ml", "l", "tsp", "tbsp", "fl oz", "cup", "pint", "quart", "gallon"]:
                u_lower = u.lower()
                if rest_lower.startswith(u_lower + " ") or rest_lower.startswith(u_lower + ","):
                    unit = canonical_unit(u)
                    rest = rest[len(u):].strip().lstrip(",")
                    break

        return {
            "display": rest,
            "quantity": qty,
            "unit": unit,
            "aisle": "",
        }

    # Plain text with no quantity/unit
    return {
        "display": line,
        "quantity": None,
        "unit": None,
        "aisle": "",
    }


@bp.post("/shopping-lists/<list_id>/clear-checked")
@login_required
def clear_checked(list_id):
    """Delete all checked items from a shopping list."""
    sl = _get_list(list_id)
    checked_items = [item for item in sl.items if item.checked]
    for item in checked_items:
        db.session.delete(item)
    db.session.commit()
    return jsonify(shopping_list_out(sl))


@bp.post("/shopping-lists/<list_id>/bulk-items")
@login_required
def bulk_add_items(list_id):
    """Add multiple items from lines of text.

    Accepts {"lines": ["2 eggs", "milk", ...]} and creates one ShoppingListItem
    per non-blank line, using quantity/unit parsing where possible.
    """
    sl = _get_list(list_id)
    data = request.get_json(force=True) or {}
    lines = data.get("lines", [])

    base_position = _next_position(sl)
    items_created = []

    for idx, line in enumerate(lines):
        parsed = _parse_bulk_item_line(line)
        if not parsed:
            continue  # Skip blank lines

        item = ShoppingListItem(
            display=parsed["display"],
            quantity=parsed.get("quantity"),
            unit=parsed.get("unit"),
            aisle=parsed.get("aisle", ""),
            position=base_position + idx,
            shopping_list_id=sl.id,
        )
        db.session.add(item)
        items_created.append(item)

    db.session.commit()
    return jsonify({
        **shopping_list_out(sl),
        "added": len(items_created),
        "items": [shopping_item_out(i) for i in items_created],
    }), 201
