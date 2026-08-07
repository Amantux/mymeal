"""Shopping consolidation groups unstructured lines by canonical food.

Scope note, checked rather than assumed: the UNIT half of this was already
correct. recipes.py canonicalises units on create, so "2 tbsp" and "3
tablespoon" already consolidated before this change and a test asserting it
would have passed either way. What did not work was the text key — an
ingredient with no structured food grouped on its raw lowercased name, so the
same food written two ways produced two lines to buy.
"""
import pytest

from app.models import Recipe
from app.services.shopping import build_from_recipes


def _two(auth_client, app, a, b):
    for name, display in (("Xa", a), ("Xb", b)):
        auth_client.post("/api/v1/recipes",
                         json={"name": name, "ingredients": [{"display": display}]})
    with app.app_context():
        recipes = [Recipe.query.filter_by(name=n).first() for n in ("Xa", "Xb")]
        return build_from_recipes(recipes)


@pytest.mark.parametrize("a, b, qty", [
    ("2 tbsp olive oil", "3 tbsp extra virgin olive oil", 5.0),
    ("100 g Vietnamese cinnamon", "50 g cinnamon", 150.0),
    ("2 tbsp finely chopped parsley", "1 tbsp parsley", 3.0),
])
def test_the_same_food_written_two_ways_is_one_line_to_buy(auth_client, app, a, b, qty):
    items = _two(auth_client, app, a, b)
    assert len(items) == 1, f"{a!r} and {b!r} produced {len(items)} lines: " \
                            f"{[i['display'] for i in items]}"
    assert items[0]["quantity"] == qty


def test_the_consolidated_line_is_named_canonically(auth_client, app):
    """You buy cinnamon. The variety is a property of the recipe line, not of
    the thing you put in the trolley."""
    items = _two(auth_client, app, "100 g Vietnamese cinnamon", "50 g cinnamon")
    assert items[0]["display"] == "cinnamon"


@pytest.mark.parametrize("a, b", [
    # ...and materially different foods still buy separately. This is the half
    # that must NOT collapse, and it is the reason the key is a canonical food
    # rather than a shared substring.
    ("2 tbsp peanut butter", "50 g butter"),
    ("1 cup almond milk", "1 cup milk"),
    ("2 tbsp rice vinegar", "100 g rice"),
])
def test_materially_different_foods_stay_separate_lines(auth_client, app, a, b):
    items = _two(auth_client, app, a, b)
    assert len(items) == 2, f"{a!r} and {b!r} were merged into one purchase"


def test_an_unrecognised_food_still_consolidates_with_itself(auth_client, app):
    """The safe failure: an unknown name is its own key, not dropped."""
    items = _two(auth_client, app, "2 tbsp quargelkase", "1 tbsp quargelkase")
    assert len(items) == 1 and items[0]["quantity"] == 3.0


def test_different_units_are_not_summed(auth_client, app):
    """Out of scope and deliberately unchanged: 100 g + 1 kg still buys as two
    lines. units.dimension() exists to fix that; nothing here does it, and
    silently adding 100 to 1 would be worse than two honest lines.
    """
    items = _two(auth_client, app, "100 g cinnamon", "1 kg cinnamon")
    assert len(items) == 2


def test_an_unrecognised_name_is_shown_exactly_as_written(auth_client, app):
    """Canonicalising text the lexicon does not understand mangles it: this
    line lost its hyphen and shopping showed "bone in chicken thighs"."""
    items = _two(auth_client, app,
                 "6 bone-in chicken thighs", "2 bone-in chicken thighs")
    assert len(items) == 1
    assert items[0]["display"] == "bone-in chicken thighs"
    assert items[0]["quantity"] == 8.0
