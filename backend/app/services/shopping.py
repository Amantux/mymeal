"""Build a consolidated shopping list from a set of recipes.

Consolidation rule: ingredients that resolve to the same canonical ``Food``
(or, lacking one, the same display text) and share a unit are merged, summing
quantities. Aisle is taken from the food so the resulting list can be grouped
for tidy shopping. Purely deterministic — no AI involved.
"""
from __future__ import annotations

from ..models import Recipe
from .components import (  # noqa: F401 — re-exported for callers/tests
    _MAX_COMPONENT_DEPTH,
    _MAX_COMPONENT_EXPANSIONS,
    iter_leaf_ingredients,
)
from .units import parse_line


def build_from_recipes(recipes: list[Recipe]) -> list[dict]:
    """Return consolidated shopping-list item dicts for the given recipes.

    A recipe used as a component (a linked sub-recipe) is expanded into its own
    ingredients so the list is a real buy-list, not a reference."""
    # key -> aggregate. key groups by (food_id or lowercased text) + unit.
    agg: dict[tuple, dict] = {}
    order: list[tuple] = []
    for recipe in recipes:
        for ing, mult in iter_leaf_ingredients(recipe):
            text = (ing.display or "").strip()
            if not text and not ing.food:
                continue
            # The display line leads with the quantity ("6 bone-in chicken
            # thighs"), but the item stores quantity/unit separately and the UI
            # prepends them — so strip the qty/unit from the name here, else it
            # renders doubled ("6 6 bone-in…"). A structured food already has a
            # clean name. Group by that name so "6 X" and "3 X" consolidate.
            if ing.food:
                name = ing.food.name
                food_key = ing.food_id
                aisle = ing.food.aisle
            else:
                name = (parse_line(text)["rest"] or text).strip()
                food_key = name.lower()
                aisle = ""
            unit = (ing.unit.abbreviation or ing.unit.name) if ing.unit else ""
            key = (food_key, unit)
            if key not in agg:
                agg[key] = {
                    "display": name,
                    "quantity": 0.0,
                    "unit": unit,
                    "aisle": aisle,
                    "foodId": ing.food_id,
                }
                order.append(key)
            agg[key]["quantity"] += float(ing.quantity or 0) * mult
    # Stable: group by aisle (unassigned last), then original insertion order.
    items = [agg[k] for k in order]
    items.sort(key=lambda i: (i["aisle"] == "", i["aisle"].lower()))
    for pos, item in enumerate(items):
        item["position"] = pos
    return items
