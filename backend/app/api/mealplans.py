from datetime import date, datetime, timedelta

from flask import Blueprint, request, jsonify, abort
from ..extensions import db
from ..models import MealPlanEntry, Recipe
from ..auth import login_required, current_group
from ..schemas.serializers import mealplan_entry_out
from ..utils import to_int

bp = Blueprint("mealplans", __name__)


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def entry_load_opts():
    """Eager-load what `mealplan_entry_out` -> `recipe_summary` actually touches:
    the recipe plus its videos, tags, categories and cook_events. Without this
    a month view cost ~4 queries per distinct recipe, and /ha/summary is
    polled on a timer."""
    from sqlalchemy.orm import selectinload
    r = selectinload(MealPlanEntry.recipe)
    return [r.selectinload(Recipe.videos),
            r.selectinload(Recipe.tags),
            r.selectinload(Recipe.categories),
            r.selectinload(Recipe.cook_events)]


def _get(entry_id) -> MealPlanEntry:
    entry = db.session.get(MealPlanEntry, entry_id)
    if not entry or entry.group_id != current_group().id:
        abort(404)
    return entry


def _valid_recipe_id(recipe_id):
    if not recipe_id:
        return None
    r = db.session.get(Recipe, recipe_id)
    return r.id if r and r.group_id == current_group().id else None


@bp.get("/plan/ingredients")
@login_required
def plan_ingredients():
    """Flattened ingredient demand for the upcoming plan.

    This is the endpoint the companion Edibl app calls
    (``/integrations/mymeal/pull``) to reconcile myMeal's plan against real
    stock. It is also what myMeal pushes to Edibl. Authenticated like any other
    endpoint, so an Edibl instance presents an API token — it is NOT public.

    Query: ?days=N (default 7) — how far ahead to include.
    """
    from datetime import date as _date

    from ..services.plan_ingredients import flatten_plan, upcoming_window

    days = to_int(request.args.get("days"), 7)
    start, end = upcoming_window(_date.today(), days)
    from sqlalchemy.orm import selectinload

    from ..services.components import component_load_opts
    entries = (
        db.session.query(MealPlanEntry)
        .filter(MealPlanEntry.group_id == current_group().id)
        .filter(MealPlanEntry.date >= start, MealPlanEntry.date <= end)
        .options(*component_load_opts(selectinload(MealPlanEntry.recipe)))
        .order_by(MealPlanEntry.date.asc())
        .all()
    )
    items = flatten_plan(entries)
    return jsonify({"items": items, "from": start.isoformat(),
                    "to": end.isoformat(), "count": len(items)})


@bp.get("/mealplans")
@login_required
def list_entries():
    gid = current_group().id
    query = (db.session.query(MealPlanEntry).filter_by(group_id=gid)
             .options(*entry_load_opts()))
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))
    if start:
        query = query.filter(MealPlanEntry.date >= start)
    if end:
        query = query.filter(MealPlanEntry.date <= end)
    # An unbounded, undated call returns EVERY entry the household has ever
    # created, each fully serialized — two years of history downloaded to answer
    # "has anything ever been planned?". `limit` bounds that; the empty-state
    # probe asks for 1.
    # Order BEFORE limiting: SQLAlchemy refuses order_by() on a query that
    # already has LIMIT applied, and "the first N by date" is the only sensible
    # reading of the two together anyway.
    query = query.order_by(MealPlanEntry.date.asc())
    limit = request.args.get("limit", type=int)
    if limit is not None:
        query = query.limit(max(1, min(limit, 500)))
    entries = query.all()
    return jsonify({"items": [mealplan_entry_out(e) for e in entries]})


@bp.post("/mealplans")
@login_required
def create_entry():
    data = request.get_json(force=True) or {}
    entry_date = _parse_date(data.get("date")) or date.today()
    # A recipeId that doesn't resolve used to be dropped silently, so the caller
    # got a 201 and a blank "Meal" slot. Say so instead. (PUT keeps its
    # clear-on-null semantics — there, an explicit null means "unlink".)
    if data.get("recipeId") and _valid_recipe_id(data["recipeId"]) is None:
        return jsonify({"error": "unknown recipeId"}), 422
    entry = MealPlanEntry(
        date=entry_date,
        meal_type=data.get("mealType", "dinner"),
        title=data.get("title", ""),
        notes=data.get("notes", ""),
        servings=to_int(data.get("servings")),
        recipe_id=_valid_recipe_id(data.get("recipeId")),
        group_id=current_group().id,
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify(mealplan_entry_out(entry)), 201


@bp.put("/mealplans/<entry_id>")
@login_required
def update_entry(entry_id):
    entry = _get(entry_id)
    data = request.get_json(force=True) or {}
    if "date" in data:
        parsed = _parse_date(data["date"])
        if parsed:
            entry.date = parsed
    if "mealType" in data:
        entry.meal_type = data["mealType"]
    if "title" in data:
        entry.title = data["title"]
    if "notes" in data:
        entry.notes = data["notes"]
    if "servings" in data:
        entry.servings = to_int(data["servings"])
    if "recipeId" in data:
        entry.recipe_id = _valid_recipe_id(data["recipeId"])
    db.session.commit()
    return jsonify(mealplan_entry_out(entry))


@bp.delete("/mealplans/<entry_id>")
@login_required
def delete_entry(entry_id):
    db.session.delete(_get(entry_id))
    db.session.commit()
    return "", 204


# Shared helper used by the shopping-list builder and AI planning.
def recipe_ids_in_range(gid, start, end) -> list[str]:
    query = db.session.query(MealPlanEntry).filter_by(group_id=gid)
    if start:
        query = query.filter(MealPlanEntry.date >= start)
    if end:
        query = query.filter(MealPlanEntry.date <= end)
    return [e.recipe_id for e in query.all() if e.recipe_id]


def entries_in_range(gid, start, end):
    """The meal-plan ENTRIES in the range (recipe + servings eager-loaded),
    NOT deduped — a recipe cooked twice appears twice so shopping sums it."""
    from sqlalchemy.orm import selectinload
    from ..services.components import component_load_opts
    query = (db.session.query(MealPlanEntry).filter_by(group_id=gid)
             .options(*component_load_opts(selectinload(MealPlanEntry.recipe))))
    if start:
        query = query.filter(MealPlanEntry.date >= start)
    if end:
        query = query.filter(MealPlanEntry.date <= end)
    return [e for e in query.order_by(MealPlanEntry.date.asc()).all() if e.recipe_id]


# Re-export for callers that pass ISO strings.
def parse_date(value):
    if isinstance(value, (date, datetime)):
        return value
    return _parse_date(value)


@bp.post("/mealplans/copy")
@login_required
def copy_mealplan():
    """Copy a range of meal plan entries to a target week.

    Body: {source_start, source_end, target_start, overwrite=false}
    - source_start/source_end: ISO date strings (inclusive), range <= 14 days
    - target_start: ISO date string for the first day of the target week
    - overwrite: bool, default false — if true, delete existing entries in copied slots

    Response: {created: N, skipped: M}
    """
    data = request.get_json(force=True) or {}

    source_start = _parse_date(data.get("source_start"))
    source_end = _parse_date(data.get("source_end"))
    target_start = _parse_date(data.get("target_start"))
    overwrite = data.get("overwrite", False)

    # Validation
    if not source_start or not source_end or not target_start:
        return jsonify({"error": "source_start, source_end, and target_start are required"}), 400

    if source_start > source_end:
        return jsonify({"error": "source_start must be <= source_end"}), 400

    # Check range is <= 14 days
    day_count = (source_end - source_start).days + 1
    if day_count > 14:
        return jsonify({"error": "source range must be <= 14 days"}), 400

    gid = current_group().id

    # Fetch source entries for the range
    from sqlalchemy.orm import selectinload
    from ..services.components import component_load_opts

    source_entries = (
        db.session.query(MealPlanEntry)
        .filter_by(group_id=gid)
        .filter(MealPlanEntry.date >= source_start, MealPlanEntry.date <= source_end)
        .options(*component_load_opts(selectinload(MealPlanEntry.recipe)))
        .order_by(MealPlanEntry.date.asc())
        .all()
    )

    # Calculate date offset
    date_offset = (target_start - source_start).days

    # Determine which target slots are occupied
    target_end = target_start + timedelta(days=day_count - 1)
    existing_entries = (
        db.session.query(MealPlanEntry)
        .filter_by(group_id=gid)
        .filter(MealPlanEntry.date >= target_start, MealPlanEntry.date <= target_end)
        .all()
    )
    existing_slots = {(e.date, e.meal_type): e for e in existing_entries}

    created_count = 0
    skipped_count = 0
    skipped_details = []

    for source_entry in source_entries:
        # Calculate target date
        target_date = source_entry.date + timedelta(days=date_offset)

        # Check if recipe belongs to current group (if it's a recipe entry)
        if source_entry.recipe_id:
            recipe = db.session.get(Recipe, source_entry.recipe_id)
            if not recipe or recipe.group_id != gid:
                # Skip foreign recipes
                skipped_count += 1
                skipped_details.append({
                    "date": source_entry.date.isoformat(),
                    "meal_type": source_entry.meal_type,
                    "reason": "foreign_recipe"
                })
                continue

        # Check for collision
        slot_key = (target_date, source_entry.meal_type)
        if slot_key in existing_slots:
            if overwrite:
                # Delete the existing entry
                db.session.delete(existing_slots[slot_key])
                db.session.flush()
            else:
                skipped_count += 1
                skipped_details.append({
                    "date": target_date.isoformat(),
                    "meal_type": source_entry.meal_type,
                    "reason": "collision"
                })
                continue

        # Create new entry
        new_entry = MealPlanEntry(
            date=target_date,
            meal_type=source_entry.meal_type,
            title=source_entry.title,
            notes=source_entry.notes,
            servings=source_entry.servings,
            recipe_id=source_entry.recipe_id,
            group_id=gid,
        )
        db.session.add(new_entry)
        created_count += 1

    db.session.commit()

    return jsonify({
        "created": created_count,
        "skipped": skipped_count,
        "skipped_details": skipped_details
    })
