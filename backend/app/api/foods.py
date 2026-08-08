"""Foods (canonical ingredients) and units of measure."""
from flask import Blueprint, request, jsonify, abort

from sqlalchemy import func
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


# Aliases are a JSON list (migration 0014) and every write goes through
# food_resolve.set_aliases — the policy chokepoint that owns the alias
# equivalence (folded keys) and the one-alias-one-food invariant. The CSV
# sanitiser/byte-cap that used to live here existed only to patrol the
# delimited column's failure modes and went with it.

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


def _existing_match(gid, name, claims=None):
    """An existing Food in this group that ``name`` already refers to.

    create_food bypassed find-or-create entirely, so the Foods screen was the
    one place in the app that could still manufacture the duplicates everything
    else works to avoid.

    Matches by FOLDED key (food_resolve.alias_key) — the same equivalence the
    ranker uses. This code used to compare raw lowercase while _rank compared
    folded keys, so "Cilantros" was created as a duplicate that the resolver
    then scored as the same food. One equivalence rule, defined once.

    Still deliberately NOT the canonical key (match_key): "Vietnamese
    cinnamon" keys apart from "cinnamon", so a variety stays creatable as its
    own food. Collapsing varieties is the merge endpoint's job, confirmed.

    claim_index iterates in (created_at, id) order, so when legacy data holds
    a collision the winner is stable across requests instead of row-order
    roulette.
    """
    key = food_resolve.alias_key(name)
    if not key:
        return None
    claims = claims if claims is not None else food_resolve.claim_index(gid)
    return claims.get(key)


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
    claims = food_resolve.claim_index(current_group().id)
    existing = _existing_match(current_group().id, data.get("name", ""), claims)
    if existing is not None:
        return jsonify({**food_out(existing), "reused": True}), 200
    food = Food(
        name=data.get("name", ""),
        plural_name=data.get("pluralName", ""),
        aisle=data.get("aisle", ""),
        description=data.get("description", ""),
        group_id=current_group().id,
    )
    db.session.add(food)
    db.session.flush()  # the policy needs food.id to recognise self-claims
    requested = data.get("aliases") or []
    if not isinstance(requested, list):
        requested = str(requested).split(",")
    _, refused = food_resolve.set_aliases(food, requested,
                                          current_group().id, claims)
    db.session.commit()
    # refusedAliases is in the body because a 201 that silently stored less
    # than the caller sent is how data loss hides.
    return jsonify({**food_out(food), "reused": False,
                    "refusedAliases": refused}), 201


@bp.put("/foods/<food_id>")
@login_required
def update_food(food_id):
    food = _get_food(food_id)
    data = request.get_json(force=True) or {}
    refused = []
    if "name" in data and data["name"] != food.name:
        # A rename is a claim like any other: without this check the
        # one-key-one-food invariant held for aliases while the NAME half of
        # the claim space stayed trivially breakable.
        key = food_resolve.alias_key(data["name"])
        owner = food_resolve.claim_index(current_group().id).get(key) \
            if key else None
        if owner is not None and owner.id != food.id:
            abort(409, description=f"'{data['name']}' already refers to "
                                   f"'{owner.name}' in this household")
        food.name = data["name"]
    elif "name" in data:
        food.name = data["name"]
    if "pluralName" in data:
        food.plural_name = data["pluralName"]
    if "aliases" in data:
        requested = data["aliases"] or []
        if not isinstance(requested, list):
            requested = str(requested).split(",")
        _, refused = food_resolve.set_aliases(food, requested,
                                              current_group().id)
    if "aisle" in data:
        food.aisle = data["aisle"]
    if "description" in data:
        food.description = data["description"]
    db.session.commit()
    # refusedAliases: a 200 that silently stored less than the user typed is
    # how data loss hides.
    return jsonify({**food_out(food), "refusedAliases": refused})


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


@bp.get("/foods/duplicates")
@login_required
def duplicate_foods():
    """Groups of Food rows that are the same ingredient written differently.

    A proposal, never an action. A migration rewriting food_id could not be
    reversed without an audit table recording every touched row, this add-on
    auto-upgrades so it would land unattended, and silently merging contradicts
    the propose-then-confirm pattern used everywhere else (see Edibl ADR-0004:
    low confidence must not silently overwrite). Reversibility comes free when
    the user saw the change and chose it.

    Executing a proposal is POST /foods/<keep>/merge with each `merge` id, so
    there is no second write path and the guards are the ones already tested.

    Grouping is by canonical key, which is what makes this safe: peanut butter
    and butter canonicalise apart and are never proposed together.
    """
    gid = current_group().id
    foods = db.session.query(Food).filter_by(group_id=gid).all()

    usage = {}
    for model in _FOOD_REFERENCES:
        for food_id, count in (db.session.query(model.food_id, func.count())
                               .filter(model.food_id.isnot(None))
                               .group_by(model.food_id)):
            usage[food_id] = usage.get(food_id, 0) + count

    groups = {}
    for food in foods:
        key = food_resolve.match_key(food.name)
        if key:
            groups.setdefault(key, []).append(food)

    def described(food):
        return {**food_out(food), "usageCount": usage.get(food.id, 0)}

    proposals = []
    for canonical, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        # Keep the row that already IS the canonical name; failing that, the one
        # used most, because that is the change touching the fewest recipes.
        # Name breaks ties so the proposal is stable between requests.
        members.sort(key=lambda f: (f.name.strip().lower() != canonical,
                                    -usage.get(f.id, 0), f.name.lower()))
        keep, rest = members[0], members[1:]
        proposals.append({
            "canonical": canonical,
            "keep": described(keep),
            "merge": [described(f) for f in rest],
        })
    return jsonify(proposals)


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

    # Order matters twice here:
    #   1. References move BEFORE the source row is deleted, or the delete
    #      violates the foreign keys it is supposed to be freeing.
    #   2. The source row is deleted (and flushed) BEFORE the alias policy
    #      runs, so its own claims are out of the claim index — otherwise the
    #      transfer of its name/aliases onto `into` would be refused as
    #      "claimed elsewhere" by the very row being merged away.
    # Source NAME first: it is the one term the carry exists for, so at the
    # count cap it must be the last thing dropped, not the first.
    transfer = ([source.name] + food_resolve.aliases_of(into)
                + food_resolve.aliases_of(source))
    # One path for both tables, and the same one delete uses — reassigning the
    # `lines` objects by hand would have covered recipe_ingredients only.
    _repoint(source.id, into.id)
    db.session.delete(source)
    db.session.flush()

    # Carry the old name and its aliases across, or the next import recreates
    # exactly the row that was just merged away. Through set_aliases, so a key
    # claimed by a THIRD food is refused rather than stolen — and the comma
    # case cannot poison the resolver: clean_alias keeps only the identity
    # part, which is all normalize_text ever lets the ranker see.
    _, refused = food_resolve.set_aliases(into, transfer, current_group().id)
    db.session.commit()
    return jsonify({**preview, "into": food_out(into), "confirmed": True,
                    "refusedAliases": refused})


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
    unit = _get_unit(unit_id)
    # Detach recipe lines that use this unit before deleting it — units.id is
    # FK-referenced by recipe_ingredients and FKs are enforced, so a bare
    # delete of an in-use unit (every auto-created "cup"/"g" is in use) 500'd.
    # Same pattern as _repoint for foods; the line keeps its display text.
    db.session.query(RecipeIngredient).filter_by(unit_id=unit.id).update(
        {"unit_id": None}, synchronize_session=False)
    db.session.delete(unit)
    db.session.commit()
    return "", 204
