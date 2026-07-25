"""Build a consolidated shopping list from a set of recipes.

Consolidation rule: ingredients that resolve to the same canonical ``Food``
(or, lacking one, the same display text) and share a unit are merged, summing
quantities. Aisle is taken from the food so the resulting list can be grouped
for tidy shopping. Purely deterministic — no AI involved.
"""
from __future__ import annotations

from ..models import Recipe


def _flatten_ingredients(recipe, seen):
    """Yield a recipe's ingredients, expanding any linked-recipe COMPONENT into
    that recipe's own ingredients (recursively). ``seen`` guards against cycles;
    a component whose target is already in the chain is left as a plain line."""
    for ing in recipe.ingredients:
        ref = ing.ref_recipe_id
        if ref and ref not in seen and ing.ref_recipe is not None:
            yield from _flatten_ingredients(ing.ref_recipe, seen | {ref})
        else:
            yield ing


def build_from_recipes(recipes: list[Recipe]) -> list[dict]:
    """Return consolidated shopping-list item dicts for the given recipes.

    A recipe used as a component (a linked sub-recipe) is expanded into its own
    ingredients so the list is a real buy-list, not a reference."""
    # key -> aggregate. key groups by (food_id or lowercased text) + unit.
    agg: dict[tuple, dict] = {}
    order: list[tuple] = []
    for recipe in recipes:
        for ing in _flatten_ingredients(recipe, frozenset({recipe.id})):
            text = (ing.display or "").strip()
            if not text and not ing.food:
                continue
            food_key = ing.food_id or text.lower()
            unit = (ing.unit.abbreviation or ing.unit.name) if ing.unit else ""
            key = (food_key, unit)
            if key not in agg:
                agg[key] = {
                    "display": ing.food.name if ing.food else text,
                    "quantity": 0.0,
                    "unit": unit,
                    "aisle": ing.food.aisle if ing.food else "",
                    "foodId": ing.food_id,
                }
                order.append(key)
            agg[key]["quantity"] += float(ing.quantity or 0)
    # Stable: group by aisle (unassigned last), then original insertion order.
    items = [agg[k] for k in order]
    items.sort(key=lambda i: (i["aisle"] == "", i["aisle"].lower()))
    for pos, item in enumerate(items):
        item["position"] = pos
    return items
