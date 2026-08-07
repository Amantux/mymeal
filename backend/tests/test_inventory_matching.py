"""Inventory coverage must respect the same material boundary as everything else.

`_ingredient_covered` asked whether an on-hand name appeared ANYWHERE in the
ingredient's text, so having rice told you that you could make a recipe calling
for rice vinegar, and an egg covered an eggplant. This is Edibl's ADR-0003
complaint ("substring never crosses an allergen/item_type edge") reproduced in
myMeal, and it is user-visible: the recipe shows as cookable and the missing
ingredient is not listed.
"""
import pytest

from app.services import inventory


class _Food:
    def __init__(self, name, aliases=None):
        self.name = name
        self.aliases = aliases


class _Ing:
    """Just enough of a RecipeIngredient for the matcher."""
    def __init__(self, display, food=None):
        self.display = display
        self.food = food


def covered(ingredient_text, on_hand, food=None):
    names = inventory._name_set([{"name": n} for n in on_hand])
    return inventory._ingredient_covered(_Ing(ingredient_text, food), names)


@pytest.mark.parametrize("ingredient, stock", [
    ("2 tbsp rice vinegar", ["rice"]),
    ("1 aubergine", ["egg"]),
    ("2 tbsp peanut butter", ["butter"]),
    ("1 cup almond milk", ["milk"]),
    ("chicken stock", ["chicken"]),
    ("1 tsp vanilla extract", ["vanilla"]),
])
def test_stock_does_not_cover_a_materially_different_ingredient(ingredient, stock):
    assert not covered(ingredient, stock), (
        f"having {stock} was reported as covering {ingredient!r}")


@pytest.mark.parametrize("ingredient, stock", [
    ("2 large eggs", ["egg"]),            # plural/singular
    ("1 large egg", ["eggs"]),
    ("2 tbsp Vietnamese cinnamon", ["cinnamon"]),   # a variety IS the food
    ("3 tbsp extra virgin olive oil", ["olive oil"]),
    ("finely chopped garlic", ["garlic"]),
    ("2 cups whole milk", ["milk"]),
])
def test_stock_covers_the_same_food_written_differently(ingredient, stock):
    assert covered(ingredient, stock), (
        f"having {stock} should cover {ingredient!r}")


def test_a_food_alias_still_counts_as_coverage():
    """Aliases were read with a fourth hand-rolled CSV split; whichever storage
    form the row uses, coverage must not depend on it."""
    assert covered("1 aubergine", ["eggplant"],
                   food=_Food("aubergine", "eggplant, brinjal"))
    assert covered("1 aubergine", ["brinjal"],
                   food=_Food("aubergine", '["eggplant", "brinjal"]'))


def test_no_stock_covers_nothing():
    assert not covered("2 eggs", [])


def test_use_it_up_expands_components_like_what_can_i_cook(auth_client, app):
    """The two rankers judged the same recipes by different rules.

    rank_recipes expanded linked sub-recipes; rank_use_it_up read
    recipe.ingredients directly. So a dish whose SUB-recipe used the expiring
    item was offered by "what can I cook" and silently missing from "use it up"
    — the one screen whose entire job is to stop that item being thrown away.
    """
    from app.models import Recipe
    from app.services.inventory import rank_recipes, rank_use_it_up

    sauce = auth_client.post("/api/v1/recipes", json={
        "name": "Basil Pesto",
        "ingredients": [{"display": "basil", "food": "basil"}],
    }).get_json()
    auth_client.post("/api/v1/recipes", json={
        "name": "Pesto Pasta",
        "ingredients": [
            {"display": "pasta", "food": "pasta"},
            {"display": "1 batch Basil Pesto", "quantity": 1, "unit": "batch",
             "refRecipeId": sauce["id"]},
        ],
    })

    expiring = [{"name": "basil", "daysLeft": 1}]
    with app.app_context():
        dish = Recipe.query.filter_by(name="Pesto Pasta").first()
        cooked = rank_recipes([dish], expiring)
        used = rank_use_it_up([dish], expiring)

    assert cooked[0]["haveCount"] >= 1
    assert used, "the dish's sub-recipe uses the expiring basil, but it was dropped"
    assert used[0]["uses"] == ["basil"]
