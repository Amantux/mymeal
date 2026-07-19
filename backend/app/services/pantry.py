"""Match recipes against what's in the pantry — 'what can I cook right now?'.

Deterministic scoring: for each recipe, count how many of its ingredients are
covered by the pantry (by canonical Food id, or by a name/alias substring
match), and rank by coverage. Feeds both the ``/ai/suggest`` endpoint and the
MCP ``what_can_i_cook`` tool.
"""
from __future__ import annotations

from ..models import Recipe, PantryItem


def _pantry_index(pantry: list[PantryItem]):
    food_ids = {p.food_id for p in pantry if p.food_id}
    names: set[str] = set()
    for p in pantry:
        if p.label:
            names.add(p.label.strip().lower())
        if p.food:
            names.add(p.food.name.strip().lower())
            for alias in (p.food.aliases or "").split(","):
                if alias.strip():
                    names.add(alias.strip().lower())
    # Drop very short tokens that would match almost anything.
    names = {n for n in names if len(n) >= 3}
    return food_ids, names


def _ingredient_covered(ing, food_ids: set[str], names: set[str]) -> bool:
    if ing.food_id and ing.food_id in food_ids:
        return True
    display = (ing.display or "").lower()
    return any(name in display for name in names)


def rank_recipes(recipes: list[Recipe], pantry: list[PantryItem]) -> list[dict]:
    """Rank recipes by how well the pantry covers their ingredients."""
    food_ids, names = _pantry_index(pantry)
    scored = []
    for recipe in recipes:
        ings = list(recipe.ingredients)
        total = len(ings)
        if total == 0:
            continue
        have = sum(1 for i in ings if _ingredient_covered(i, food_ids, names))
        missing = [
            i.display for i in ings if not _ingredient_covered(i, food_ids, names)
        ]
        scored.append(
            {
                "recipeId": recipe.id,
                "name": recipe.name,
                "slug": recipe.slug,
                "haveCount": have,
                "totalCount": total,
                "missingCount": total - have,
                "coverage": round(have / total, 3),
                "missing": missing,
            }
        )
    scored.sort(key=lambda s: (-s["coverage"], s["missingCount"]))
    return scored
