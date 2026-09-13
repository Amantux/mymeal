"""Build a consolidated shopping list from a set of recipes.

Consolidation rule: ingredients that resolve to the same canonical ``Food``
(or, lacking one, the same display text) and share a unit are merged, summing
quantities. Aisle is taken from the food so the resulting list can be grouped
for tidy shopping. Purely deterministic — no AI involved.
"""
from __future__ import annotations

from ..models import Recipe
from . import food_resolve
from .components import (  # noqa: F401 — re-exported for callers/tests
    _MAX_COMPONENT_DEPTH,
    _MAX_COMPONENT_EXPANSIONS,
    iter_leaf_ingredients,
)
from .units import parse_line


def build_from_recipes(recipes: list[Recipe]) -> list[dict]:
    """Consolidated shopping-list item dicts for the given recipes (each ×1)."""
    return build_from_entries([(r, 1.0) for r in recipes])


def build_from_entries(pairs) -> list[dict]:
    """Like :func:`build_from_recipes` but each entry carries its own
    multiplier — so a meal-plan that cooks a recipe twice, or at double
    servings, buys the summed, scaled quantities rather than one unscaled copy.

    A recipe used as a component (a linked sub-recipe) is expanded into its own
    ingredients so the list is a real buy-list, not a reference."""
    # key -> aggregate. key groups by (food_id or lowercased text) + unit.
    agg: dict[tuple, dict] = {}
    order: list[tuple] = []
    for recipe, extra in pairs:
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
                # A declared free-text line is prose, not a qty/unit/food split,
                # and it deliberately stores no quantity or unit — so there is
                # nothing for the UI to prepend and stripping the leading amount
                # simply DELETES it ("2 handfuls of rocket, to serve" became
                # "of rocket, to serve" × 0). Keep the author's line whole; that
                # is the point of the lane.
                name = (text if ing.free_text
                        else (parse_line(text)["rest"] or text)).strip()
                # Group by canonical food, not by the raw text. Grouping on
                # name.lower() meant "olive oil" and "extra virgin olive oil"
                # were two things to buy, and so were "cinnamon" and
                # "Vietnamese cinnamon" — you would come home with two jars.
                #
                # An unrecognised name canonicalises to itself, so it still
                # consolidates with itself and is never dropped. Materially
                # different foods (peanut butter vs butter) canonicalise apart
                # and correctly stay separate purchases.
                canonical = food_resolve.match_key(name)
                food_key = canonical or name.lower()
                # Buy the canonical thing: the variety belongs to the recipe
                # line, not to what goes in the trolley. Only rename a food we
                # actually recognise, though — canonicalising text we do not
                # understand mangles it ("bone-in chicken thighs" loses its
                # hyphen). Unknown names still group by their canonical key, so
                # two spellings of the same unknown thing merge; the label just
                # stays as the user wrote it.
                # ...but never for a declared free-text line. Grouping by the
                # canonical key is still right (two prose lines about butter
                # belong together), yet RELABELLING would throw the author's
                # sentence away exactly like the strip above did — "a good knob
                # of butter, for finishing" would reach the list as "butter".
                # The lane's whole promise is that the human's line survives.
                if canonical and food_resolve.is_known(canonical) and not ing.free_text:
                    name = canonical
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
            agg[key]["quantity"] += float(ing.quantity or 0) * mult * extra
    # Stable: group by aisle (unassigned last), then original insertion order.
    items = [agg[k] for k in order]
    items.sort(key=lambda i: (i["aisle"] == "", i["aisle"].lower()))
    for pos, item in enumerate(items):
        item["position"] = pos
    return items
