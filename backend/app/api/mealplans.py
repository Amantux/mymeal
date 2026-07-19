from datetime import date, datetime

from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import MealPlanEntry, Recipe
from ..auth import login_required, current_group
from ..schemas.serializers import mealplan_entry_out

bp = Blueprint("mealplans", __name__)


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


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


@bp.get("/mealplans")
@login_required
def list_entries():
    gid = current_group().id
    query = db.session.query(MealPlanEntry).filter_by(group_id=gid)
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))
    if start:
        query = query.filter(MealPlanEntry.date >= start)
    if end:
        query = query.filter(MealPlanEntry.date <= end)
    entries = query.order_by(MealPlanEntry.date.asc()).all()
    return jsonify({"items": [mealplan_entry_out(e) for e in entries]})


@bp.post("/mealplans")
@login_required
def create_entry():
    data = request.get_json(force=True) or {}
    entry_date = _parse_date(data.get("date")) or date.today()
    entry = MealPlanEntry(
        date=entry_date,
        meal_type=data.get("mealType", "dinner"),
        title=data.get("title", ""),
        notes=data.get("notes", ""),
        servings=int(data.get("servings") or 0),
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
        entry.servings = int(data["servings"] or 0)
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


# Re-export for callers that pass ISO strings.
def parse_date(value):
    if isinstance(value, (date, datetime)):
        return value
    return _parse_date(value)
