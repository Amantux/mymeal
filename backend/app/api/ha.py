"""Consolidated read endpoints for the Home Assistant integration.

* ``/api/v1/ha/summary``  – one cheap poll with counts + today's/this week's
  meals, unchecked shopping items, and soon-to-expire pantry items.
* ``/api/v1/ha/calendar`` – meal-plan entries as calendar events, consumed by
  the myMeal Calendar entity.
"""
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from ..extensions import db
from ..models import Recipe, MealPlanEntry, PantryItem, ShoppingList, ShoppingListItem
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

    pantry = db.session.query(PantryItem).filter_by(group_id=gid).all()
    expiring = [
        p for p in pantry if p.expires_at and today <= p.expires_at <= week_end
    ]

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
                "pantryItems": len(pantry),
                "pantryExpiring": len(expiring),
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
            "pantryExpiring": [
                {"name": p.label, "expires": p.expires_at.isoformat()}
                for p in sorted(expiring, key=lambda x: x.expires_at)
            ],
        }
    )


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
