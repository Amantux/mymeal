"""Foods (canonical ingredients) and units of measure."""
from flask import Blueprint, request, jsonify, abort

from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Food, RecipeIngredient, ShoppingListItem, Unit
from ..auth import login_required, current_group
from ..schemas.serializers import food_out, unit_out
from ..services import food_resolve

bp = Blueprint("foods", __name__)


# --- Foods ---------------------------------------------------------------
def _get_food(food_id):
    food = db.session.get(Food, food_id)
    if not food or food.group_id != current_group().id:
        abort(404)
    return food


# Food.aliases is a comma-separated String(512). Both facts bite:
#   * a term containing a comma becomes TWO aliases - merging a food named
#     "salt, kosher" gave cinnamon the aliases ['salt', 'kosher'], after which
#     POST /foods {"name": "salt"} returned cinnamon and every DB-backed
#     resolver inherited the same wrong mapping.
#   * merge only ever appends, so repeated merges exceed 512 chars. SQLite
#     ignores VARCHAR length so it looks fine; Postgres rejects the value as
#     too long, and then even a plain PUT on that row fails.
_ALIAS_MAX_CHARS = 500


def _clean_alias(term) -> str:
    """One alias, safe to store in a comma-delimited column."""
    return " ".join(str(term or "").replace(",", " ").split())


def _aliases_str(value):
    if not isinstance(value, list):
        value = [v for v in str(value or "").split(",")]
    out, total = [], 0
    for item in value:
        term = _clean_alias(item)
        if not term or term.lower() in {o.lower() for o in out}:
            continue
        # +1 for the joining comma. Stop at a whole term rather than cutting one
        # in half, so a stored alias is always a complete alias.
        if total + len(term) + (1 if out else 0) > _ALIAS_MAX_CHARS:
            break
        total += len(term) + (1 if out else 0)
        out.append(term)
    return ",".join(out)


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


def _existing_match(gid, name):
    """An existing Food in this group that ``name`` already refers to.

    create_food bypassed find-or-create entirely, so the Foods screen was the
    one place in the app that could still manufacture the duplicates everything
    else works to avoid.

    Deliberately matches on the NAME or an alias only, never on the canonical
    key. Creating a food here is an explicit act: someone typed a distinct name
    on the Foods screen, and "Vietnamese cinnamon" canonicalises to "cinnamon",
    so a canonical match would make it impossible to keep a variety as its own
    food - the exact thing _find_or_create_food goes out of its way to protect.
    Collapsing varieties is what the merge endpoint is for, with a confirmation.
    """
    raw = (name or "").strip()
    if not raw:
        return None
    for food in db.session.query(Food).filter_by(group_id=gid).all():
        terms = [food.name] + food_resolve.aliases_of(food)
        if raw.lower() in {t.strip().lower() for t in terms if t}:
            return food
    return None


@bp.post("/foods")
@login_required
def create_food():
    data = request.get_json(force=True) or {}
    # 200 (not 201) so a caller can tell "reused" from "created" — and no error,
    # because asking for a food that exists is not a failure, it is the outcome
    # the user wanted. `reused` is in the BODY too: a client that only reads the
    # body cannot see the status code, and would otherwise assume its aisle and
    # description had been stored.
    #
    # The supplied fields are deliberately NOT applied to the existing row. A
    # create must not silently change something that already exists; editing is
    # what PUT is for.
    existing = _existing_match(current_group().id, data.get("name", ""))
    if existing is not None:
        return jsonify({**food_out(existing), "reused": True}), 200
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
    return jsonify({**food_out(food), "reused": False}), 201


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


# Every table with a foreign key to foods.id. Both must be handled by delete
# AND by merge: handling only recipe_ingredients still violated the constraint
# via shopping_list_items, and a merge that moved only the recipe references
# left shopping lines pointing at a row it was about to delete.
#
# If a new FK to foods.id is added, it belongs here — grep is:
#   grep -rn 'ForeignKey("foods.id")' app/models/
_FOOD_REFERENCES = (RecipeIngredient, ShoppingListItem)


def _repoint(food_id, new_food_id):
    """Move every reference to ``food_id`` onto ``new_food_id`` (or None).

    The app enables FK enforcement, so deleting a referenced Food raised
    IntegrityError and the endpoint 500'd — only a food nothing used could be
    deleted. Doing it explicitly (the pattern delete_recipe already uses for
    component links) keeps the guarantee on installs whose migrated schema has
    no ON DELETE SET NULL.

    The recipe and shopping LINES survive: they are what the user wrote, and
    the food is only the classification hung off them.

    synchronize_session="fetch" so objects already loaded in this request do not
    keep a stale food_id and write it back on the next flush.
    """
    count = 0
    for model in _FOOD_REFERENCES:
        rows = db.session.query(model).filter_by(food_id=food_id)
        count += rows.count()
        rows.update({"food_id": new_food_id}, synchronize_session="fetch")
    return count


@bp.delete("/foods/<food_id>")
@login_required
def delete_food(food_id):
    food = _get_food(food_id)
    _repoint(food.id, None)
    db.session.delete(food)
    db.session.commit()
    return "", 204


@bp.post("/foods/<food_id>/merge")
@login_required
def merge_food(food_id):
    """Move every reference from ``fromId`` onto this food, then delete it.

    Two-step: the first call previews what would move and what is lost, naming
    the recipes affected; only ``confirm: true`` acts. Consolidating duplicates
    is a destructive operation on data the user curated, and it is not
    reversible from the UI.
    """
    into = _get_food(food_id)
    data = request.get_json(force=True) or {}
    raw_from = data.get("fromId")
    # An explicit type check, not str(): a JSON number/object/list raised
    # AttributeError from .strip() and 500'd on an untrusted boundary, and
    # str()-ing it would turn junk into a plausible-looking id.
    if not isinstance(raw_from, str):
        abort(400, description="fromId must be a string")
    from_id = raw_from.strip()
    if not from_id:
        abort(400, description="fromId is required")
    if from_id == into.id:
        abort(400, description="cannot merge a food into itself")
    # _get_food scopes to the current group, so this is the tenant check for the
    # SECOND id. A merge takes two, and checking only the URL's id would let one
    # household delete another's food.
    source = _get_food(from_id)

    lines = (db.session.query(RecipeIngredient)
             # eager: the preview reads line.recipe.name, which was one SELECT
             # per line.
             .options(selectinload(RecipeIngredient.recipe))
             .filter_by(food_id=source.id).all())
    recipes = sorted({line.recipe.name for line in lines if line.recipe})
    shopping = (db.session.query(ShoppingListItem)
                .filter_by(food_id=source.id).count())
    preview = {
        "from": food_out(source),
        "into": food_out(into),
        "ingredientCount": len(lines),
        "shoppingItemCount": shopping,
        "recipes": recipes,
    }
    if not data.get("confirm"):
        return jsonify({**preview, "confirmed": False})

    # Carry the old name and its aliases across, or the next import recreates
    # exactly the row that was just merged away.
    aliases = [a for a in food_resolve.aliases_of(into)]
    for term in [source.name] + food_resolve.aliases_of(source):
        term = (term or "").strip()
        if term and term.lower() != into.name.lower() \
                and term.lower() not in {a.lower() for a in aliases}:
            aliases.append(term)
    into.aliases = _aliases_str(aliases)

    # One path for both tables, and the same one delete uses — reassigning the
    # `lines` objects by hand would have covered recipe_ingredients only.
    _repoint(source.id, into.id)
    db.session.delete(source)
    db.session.commit()
    return jsonify({**preview, "into": food_out(into), "confirmed": True})


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
