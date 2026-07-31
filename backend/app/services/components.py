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


def _component_mult(ing) -> float:
    """A component row's quantity is its batch multiplier; 0/blank means 1×."""
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
