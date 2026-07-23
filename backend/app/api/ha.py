"""Consolidated read endpoints for the Home Assistant integration.

* ``/api/v1/ha/summary``  – one cheap poll with counts + today's/this week's
  meals and unchecked shopping items.
* ``/api/v1/ha/calendar`` – meal-plan entries as calendar events, consumed by
  the myMeal Calendar entity.
"""
from datetime import date, timedelta

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from ..extensions import db
from ..models import Recipe, MealPlanEntry, ShoppingList, ShoppingListItem
from ..auth import login_required, current_group

bp = Blueprint("ha", __name__)


def _parse(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _entry_label(e):
    return e.recipe.name if e.recipe else (e.title or "Meal")


@bp.get("/ha/summary")
@login_required
def summary():
    gid = current_group().id
    today = date.today()
    week_end = today + timedelta(days=7)

    recipes = db.session.query(Recipe).filter_by(group_id=gid).count()

    week_entries = (
        db.session.query(MealPlanEntry)
        .filter(
            MealPlanEntry.group_id == gid,
            MealPlanEntry.date >= today,
            MealPlanEntry.date < week_end,
        )
        .order_by(MealPlanEntry.date.asc())
        .all()
    )
    todays = [e for e in week_entries if e.date == today]

    unchecked = (
        db.session.query(ShoppingListItem)
        .join(ShoppingList, ShoppingList.id == ShoppingListItem.shopping_list_id)
        .filter(ShoppingList.group_id == gid, ShoppingListItem.checked.is_(False))
        .count()
    )

    return jsonify(
        {
            "health": True,
            "group": current_group().name,
            "totals": {
                "recipes": recipes,
                "mealsThisWeek": len(week_entries),
                "shoppingItems": unchecked,
            },
            "todaysMeals": [
                {"mealType": e.meal_type, "name": _entry_label(e)} for e in todays
            ],
            "weekPlan": [
                {
                    "date": e.date.isoformat(),
                    "mealType": e.meal_type,
                    "name": _entry_label(e),
                }
                for e in week_entries
            ],
        }
    )


@bp.get("/ha/version")
@login_required
def version():
    """A cheap change-cursor for the household's view data.

    The frontend polls this (far smaller than /summary) and only refetches real
    data when the token changes — catching edits from the chat assistant, the
    MCP server, Home Assistant, or another device, all of which write the same
    DB. COUNT together with MAX(updated_at) detects inserts, updates (updated_at
    has onupdate), AND deletes, across the tables the views render.
    """
    gid = current_group().id

    def stamp(count, latest):
        return f"{count}@{latest.isoformat() if latest else '0'}"

    rc, ru = (
        db.session.query(func.count(Recipe.id), func.max(Recipe.updated_at))
        .filter(Recipe.group_id == gid)
        .one()
    )
    mc, mu = (
        db.session.query(
            func.count(MealPlanEntry.id), func.max(MealPlanEntry.updated_at)
        )
        .filter(MealPlanEntry.group_id == gid)
        .one()
    )
    sc, su = (
        db.session.query(
            func.count(ShoppingListItem.id), func.max(ShoppingListItem.updated_at)
        )
        .join(ShoppingList, ShoppingList.id == ShoppingListItem.shopping_list_id)
        .filter(ShoppingList.group_id == gid)
        .one()
    )

    token = "|".join((stamp(rc, ru), stamp(mc, mu), stamp(sc, su)))
    return jsonify({"v": token})


@bp.get("/ha/calendar")
@login_required
def calendar():
    gid = current_group().id
    start = _parse(request.args.get("start")) or (date.today() - timedelta(days=7))
    end = _parse(request.args.get("end")) or (date.today() + timedelta(days=30))

    entries = (
        db.session.query(MealPlanEntry)
        .filter(
            MealPlanEntry.group_id == gid,
            MealPlanEntry.date >= start,
            MealPlanEntry.date <= end,
        )
        .order_by(MealPlanEntry.date.asc())
        .all()
    )
    events = [
        {
            "uid": f"meal-{e.id}",
            "summary": f"{e.meal_type.title()}: {_entry_label(e)}",
            "start": e.date.isoformat(),
            "end": (e.date + timedelta(days=1)).isoformat(),
            "category": e.meal_type,
            "recipeId": e.recipe_id,
        }
        for e in entries
    ]
    return jsonify(events)
