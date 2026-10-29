"""Expand linked-recipe COMPONENTS (sub-recipes) into their real contents.

A recipe ingredient row may carry ``ref_recipe_id`` — a link to another recipe
used as a component (e.g. French onion soup → a beef-stock sub-recipe), with the
row's ``quantity`` acting as a multiplier ("2 batches"). Historically only the
shopping-list builder expanded these; this module is the single, reusable
expander so meal-plan demand, inventory matching, and nutrition all agree.

Two views over the same recursion, both cycle-guarded and bounded so a
pathological/adversarial nest can't fan out exponentially:
  - ``iter_leaf_ingredients`` → the flat leaf ingredients (components replaced by
    their sub-recipe's ingredients), each with an effective multiplier.
  - ``iter_recipe_tree`` → every recipe node in the component tree (self first),
    each with its path multiplier — for summing per-recipe data like nutrition.

Beyond the limits a component is left un-expanded (yielded as its own line /
not recursed) rather than dropped, so the caller degrades gracefully.
"""
from __future__ import annotations

_MAX_COMPONENT_DEPTH = 4
_MAX_COMPONENT_EXPANSIONS = 100

#: The same caps, public, for a caller that needs a different output SHAPE than
#: the two iterators below produce. Allergen screening is the case: it builds a
#: TREE (foodcore nests a component's contents under the parent line so the
#: parent's own text stops being treated as opaque), which neither a flat leaf
#: list nor a per-recipe walk can express. It must still obey the same cycle
#: guard and the same limits — exported rather than copied so there is one
#: place to change them.
MAX_COMPONENT_DEPTH = _MAX_COMPONENT_DEPTH
MAX_COMPONENT_EXPANSIONS = _MAX_COMPONENT_EXPANSIONS


def component_load_opts(base=None):
    """Eager-load options for the component ingredient graph (ingredient → food/unit,
    plus one hop through a linked component recipe's ingredients), so expanding
    components off a list of recipes doesn't fire a query per row. Deeper nesting
    falls back to lazy loads bounded by the expansion caps above.

    ``base`` chains the loader from a parent relationship — omit for a ``Recipe``
    root, or pass e.g. ``selectinload(MealPlanEntry.recipe)`` to load through a plan
    entry. Shared by shopping, meal-plan, and inventory so all four sites agree."""
    from sqlalchemy.orm import selectinload

    from ..models import Recipe, RecipeIngredient

    ing = (base.selectinload(Recipe.ingredients) if base is not None
           else selectinload(Recipe.ingredients))
    return [
        ing.selectinload(RecipeIngredient.food),
        ing.selectinload(RecipeIngredient.unit),
        ing.selectinload(RecipeIngredient.ref_recipe)
        .selectinload(Recipe.ingredients).selectinload(RecipeIngredient.food),
    ]


#: Units on a component row that mean "this many whole makings of it".
#:
#: A component's quantity is a BATCH MULTIPLIER, so it is only meaningful when
#: the unit counts whole batches. "1 unit", "2 batches" and a bare "3" all do.
#: "2 cups" does not: reading it as 2 batches is unboundedly wrong (a stock
#: yielding 8 cups would be over-bought 4x, and the error grows with the
#: recipe's yield), and reading it correctly needs a declared yield in a
#: compatible unit, which recipes do not carry.
#:
#: "x" and "×" are here because "×2" is how the amount reads in the UI.
_MULTIPLE_UNITS = frozenset({
    "", "batch", "batches", "unit", "units", "portion", "portions",
    "serving", "servings", "recipe", "recipes", "x", "\u00d7", "lot", "lots",
    "quantity", "qty", "each", "ea", "whole", "time", "times",
})


def component_unit_is_multiple(unit_name: str | None) -> bool:
    """Whether `unit_name` on a component row counts whole batches.

    Exposed so a caller can WARN rather than silently mis-scale -- the API
    surfaces it on the row, and the editor only offers units that pass.
    """
    return str(unit_name or "").strip().lower() in _MULTIPLE_UNITS


def component_multiplier(ing) -> float:
    """Public name for a component row's batch multiplier.

    The API needs it to expand ONE component row (rather than a whole recipe)
    for the detail view, and reaching into a private helper from there is how a
    second, drifting copy of this rule gets written.
    """
    return _component_mult(ing)


def _component_mult(ing) -> float:
    """A component row's quantity as a batch multiplier; 0/blank means 1x.

    ⚠️ A quantity in a unit that does NOT count whole batches is read as 1x,
    not as itself. Previously the unit was ignored outright, so "2 cups
    oxtail stock" expanded to TWO FULL BATCHES of stock in every shopping
    list, nutrition total and can-I-cook check -- silently, and reachable
    through the API today (the editor only ever writes "batch", but the AI
    parser, MCP and the HA integration all post rows with a unit).

    1x is not the true answer either -- that needs a yield this app does not
    record -- but it is the CONSERVATIVE reading and the one already documented
    as the default. It is also bounded: wrong by the recipe's yield rather than
    by yield x quantity. The row is flagged to the client so a human can fix
    the thing a guess cannot.
    """
    unit_name = getattr(getattr(ing, "unit", None), "name", None)
    if not component_unit_is_multiple(unit_name):
        return 1.0
    return float(ing.quantity or 0) or 1.0


def iter_leaf_ingredients(recipe, mult: float = 1.0):
    """Yield ``(ingredient, multiplier)`` for a recipe, expanding any linked
    component into its sub-recipe's ingredients (recursively). A component that
    can't be expanded (cycle, cap, missing target) is yielded as a plain line."""
    yield from _leaves(recipe, frozenset({recipe.id}), {"n": 0}, 0, mult)


def _leaves(recipe, seen, state, depth, mult):
    for ing in recipe.ingredients:
        ref = getattr(ing, "ref_recipe_id", None)
        if (ref and ref not in seen and ing.ref_recipe is not None
                and depth < _MAX_COMPONENT_DEPTH
                and state["n"] < _MAX_COMPONENT_EXPANSIONS):
            state["n"] += 1
            yield from _leaves(ing.ref_recipe, seen | {ref}, state,
                               depth + 1, mult * _component_mult(ing))
        else:
            yield ing, mult


def iter_recipe_tree(recipe, mult: float = 1.0):
    """Yield ``(recipe, multiplier)`` for the recipe and every component sub-recipe
    in its tree (self first), path-multiplier accumulated. Same caps/cycle guard."""
    yield from _walk(recipe, frozenset({recipe.id}), {"n": 0}, 0, mult)


def _walk(recipe, seen, state, depth, mult):
    yield recipe, mult
    if depth >= _MAX_COMPONENT_DEPTH:
        return
    for ing in recipe.ingredients:
        ref = getattr(ing, "ref_recipe_id", None)
        if (ref and ref not in seen and ing.ref_recipe is not None
                and state["n"] < _MAX_COMPONENT_EXPANSIONS):
            state["n"] += 1
            yield from _walk(ing.ref_recipe, seen | {ref}, state,
                             depth + 1, mult * _component_mult(ing))
