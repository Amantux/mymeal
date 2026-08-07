"""Match recipes against on-hand inventory — "what can I cook right now?".

Inventory is owned by the companion Edibl app, not by myMeal. myMeal keeps the
matching logic (which is about recipes, its own domain) but no longer stores a
pantry. ``on_hand`` here is a list of ``{"name": ...}`` items — exactly what
``EdiblClient.get_stock()`` returns — so the matcher is agnostic to where the
inventory came from.

Deterministic scoring: for each recipe, count how many ingredients are covered
by an on-hand item (matched as canonical foods, not substrings), and rank by
coverage.
"""
from __future__ import annotations

from . import food_resolve
from .components import iter_leaf_ingredients


def _name_set(on_hand: list[dict]) -> set[str]:
    """On-hand item names as canonical food keys.

    The raw name is kept too: Edibl stock is free text and may name something
    myMeal's lexicon has never heard of, where the canonical form IS the raw
    form and keeping both costs nothing.
    """
    names: set[str] = set()
    for item in on_hand:
        raw = (item.get("name") or "").strip().lower()
        # Drop very short tokens that would match almost any ingredient.
        if len(raw) < 3:
            continue
        names.add(raw)
        key = food_resolve.match_key(raw)
        if key:
            names.add(key)
    return names


def _ingredient_keys(ing) -> set[str]:
    """Every canonical food key this ingredient could be known by.

    Canonical keys rather than a text haystack, because a haystack can only be
    searched by substring and substring is exactly what gets this wrong: "rice"
    appears in "rice vinegar" and "egg" in "eggplant", so having one reported
    the other as covered. Comparing whole canonical foods cannot do that, while
    still matching "2 large eggs" to on-hand "egg" and "Vietnamese cinnamon" to
    on-hand "cinnamon" — the cases a substring search was really there for.
    """
    texts = [ing.display or ""]
    if ing.food:
        texts.append(ing.food.name or "")
        # aliases_of, not a fourth hand-rolled CSV split — this was the last one.
        texts += food_resolve.aliases_of(ing.food)
    keys = set()
    for text in texts:
        key = food_resolve.match_key(text)
        if key:
            keys.add(key)
    return keys


def _ingredient_covered(ing, names: set[str]) -> bool:
    """True if an on-hand item is the same food as this ingredient.

    Name-based because Edibl stock has no myMeal food ids.
    """
    return bool(_ingredient_keys(ing) & names)


def rank_recipes(recipes: list, on_hand: list[dict]) -> list[dict]:
    """Rank recipes by how well the on-hand inventory covers their ingredients."""
    names = _name_set(on_hand)
    scored = []
    for recipe in recipes:
        # Expand linked components so a sub-recipe's ingredients count toward the
        # match (a French onion soup that links a stock recipe is judged on the
        # stock's ingredients too, not one opaque "stock" line).
        ings = [ing for ing, _ in iter_leaf_ingredients(recipe)]
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


def rank_use_it_up(recipes: list, expiring: list[dict]) -> list[dict]:
    """Rank recipes by how many of the (expiring) items they would use up.

    ``expiring`` items may carry a ``daysLeft`` so the soonest-to-expire item a
    recipe uses can break ties. Recipes that use none of the items are dropped.
    """
    # Map each searchable name back to its item so we can report what's used.
    by_name = {}
    for item in expiring:
        raw = (item.get("name") or "").strip().lower()
        if len(raw) < 3:
            continue
        by_name.setdefault(raw, item)
        key = food_resolve.match_key(raw)
        if key:
            by_name.setdefault(key, item)
    scored = []
    for recipe in recipes:
        # Expand components, exactly as rank_recipes does. These two ranked the
        # same recipes by different rules: a dish whose sub-recipe used the
        # expiring item was suggested by "what can I cook" and silently absent
        # from "use it up".
        ings = [ing for ing, _ in iter_leaf_ingredients(recipe)]
        if not ings:
            continue
        used = set()
        for ing in ings:
            used |= _ingredient_keys(ing) & set(by_name)
        if not used:
            continue
        used_items = [by_name[n] for n in used]
        soonest = min((i.get("daysLeft") for i in used_items
                       if i.get("daysLeft") is not None), default=None)
        scored.append({
            "recipeId": recipe.id,
            "name": recipe.name,
            "slug": recipe.slug,
            "uses": sorted({by_name[n].get("name") for n in used}),
            "usesCount": len(used),
            "soonestDaysLeft": soonest,
        })
    # Most items used first; then the one racing the soonest expiry.
    scored.sort(key=lambda s: (-s["usesCount"],
                               s["soonestDaysLeft"] if s["soonestDaysLeft"] is not None else 999))
    return scored
