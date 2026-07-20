"""Flatten upcoming meal-plan entries into a flat ingredient demand list.

This is the shared shape both halves of the Edibl integration speak: what
ingredients myMeal's plan will need, and by when. Edibl's
``/integrations/mymeal/pull`` reads it from ``GET /api/v1/plan/ingredients``,
and myMeal's push sends the same structure to Edibl's
``/integrations/mymeal/plan``.

Item shape (Edibl's documented contract):
    {name, quantity, unit, neededBy, sourceRef, meal}
"""
from __future__ import annotations

from datetime import date, timedelta


def _ingredient_name(ing) -> str:
    """Prefer the canonical food name; fall back to the free-text display."""
    if ing.food and ing.food.name:
        return ing.food.name
    return (ing.display or "").strip()


def _unit_name(ing) -> str:
    if ing.unit and (ing.unit.abbreviation or ing.unit.name):
        return ing.unit.abbreviation or ing.unit.name
    return "count"


def _servings_multiplier(entry, recipe) -> float:
    """Scale by how many servings the plan asked for vs the recipe's yield.

    Only when BOTH are known and positive — a recipe with servings=0 (unset)
    must not divide, and an entry with servings=0 means 'as written'.
    """
    if entry.servings and recipe.servings and recipe.servings > 0:
        return entry.servings / recipe.servings
    return 1.0


def flatten_plan(entries) -> list[dict]:
    """Turn meal-plan entries into a demand list.

    Entries without a recipe (free-text meals) contribute no ingredients — they
    have no breakdown to reconcile against stock — and are skipped rather than
    emitted as a single opaque line.
    """
    items: list[dict] = []
    for entry in entries:
        recipe = entry.recipe
        if recipe is None:
            continue
        mult = _servings_multiplier(entry, recipe)
        needed_by = entry.date.isoformat() if entry.date else None
        for ing in recipe.ingredients:
            name = _ingredient_name(ing)
            if not name:
                continue
            qty = (ing.quantity or 0) * mult
            items.append({
                "name": name,
                "quantity": round(qty, 3) if qty else None,
                "unit": _unit_name(ing),
                "neededBy": needed_by,
                # Stable key so a re-push updates rather than duplicates in Edibl.
                "sourceRef": f"mymeal:recipe:{recipe.id}",
                "meal": entry.meal_type or "",
            })
    return items


def upcoming_window(today: date, days: int) -> tuple[date, date]:
    """[today, today+days] — the plan horizon we consider 'upcoming'."""
    days = max(0, min(days, 365))
    return today, today + timedelta(days=days)
