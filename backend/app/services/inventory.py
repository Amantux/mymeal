"""Match recipes against on-hand inventory — "what can I cook right now?".

Inventory is owned by the companion Edibl app, not by myMeal. myMeal keeps the
matching logic (which is about recipes, its own domain) but no longer stores a
pantry. ``on_hand`` here is a list of ``{"name": ...}`` items — exactly what
``EdiblClient.get_stock()`` returns — so the matcher is agnostic to where the
inventory came from.

Deterministic scoring: for each recipe, count how many ingredients are covered
by an on-hand item (name / alias substring match), and rank by coverage.
"""
from __future__ import annotations


def _name_set(on_hand: list[dict]) -> set[str]:
    names: set[str] = set()
    for item in on_hand:
        name = (item.get("name") or "").strip().lower()
        # Drop very short tokens that would match almost any ingredient.
        if len(name) >= 3:
            names.add(name)
    return names


def _ingredient_covered(ing, names: set[str]) -> bool:
    """True if any on-hand name appears in the ingredient's text, food name,
    or a food alias. Name-based because Edibl stock has no myMeal food ids."""
    haystacks = [(ing.display or "").lower()]
    if ing.food:
        haystacks.append((ing.food.name or "").lower())
        haystacks += [a.strip().lower()
                      for a in (ing.food.aliases or "").split(",") if a.strip()]
    hay = " ".join(h for h in haystacks if h)
    return any(name in hay for name in names)


def rank_recipes(recipes: list, on_hand: list[dict]) -> list[dict]:
    """Rank recipes by how well the on-hand inventory covers their ingredients."""
    names = _name_set(on_hand)
    scored = []
    for recipe in recipes:
        ings = list(recipe.ingredients)
        total = len(ings)
        if total == 0:
            continue
        covered = [_ingredient_covered(i, names) for i in ings]
        have = sum(covered)
        scored.append({
            "recipeId": recipe.id,
            "name": recipe.name,
            "slug": recipe.slug,
            "haveCount": have,
            "totalCount": total,
            "missingCount": total - have,
            "coverage": round(have / total, 3),
            "missing": [i.display for i, c in zip(ings, covered) if not c],
        })
    scored.sort(key=lambda s: (-s["coverage"], s["missingCount"]))
    return scored
