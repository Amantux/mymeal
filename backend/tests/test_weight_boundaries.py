"""Weight lookups must not cross a material boundary.

Two independent caches keyed off free text, both of which leaked between
different foods:

* ``conversions.food_term`` kept only the LAST word, so "2 sticks peanut
  butter" keyed as ("stick", "butter") and a learned "1 stick butter = 113 g"
  was served for peanut butter.
* ``units._density_for`` took the longest density-table key appearing anywhere
  in the text, so "rice vinegar" was given rice's 0.85 g/ml.

Neither is cosmetic: both silently produce a wrong number in the weight view.
"""
import pytest

from app.services import units
from app.services.conversions import food_term


@pytest.mark.parametrize("a, b", [
    # The reported bug: a nut butter is not butter.
    ("2 sticks peanut butter", "2 sticks butter"),
    ("almond butter", "butter"),
    # Non-dairy and processed milks are not milk, and not each other.
    ("1 can coconut milk", "1 can milk"),
    ("1 can evaporated milk", "1 can coconut milk"),
    ("condensed milk", "milk"),
    # Same head noun, different food entirely.
    ("rice vinegar", "rice"),
    ("chicken stock", "chicken"),
    ("vanilla extract", "vanilla"),
])
def test_materially_different_foods_do_not_share_a_cache_key(a, b):
    assert food_term(a) != food_term(b), (
        f"{a!r} and {b!r} share the key {food_term(a)!r}, so a weight learned "
        f"for one is served for the other")


@pytest.mark.parametrize("a, b", [
    # Preparation and grade never change what a thing weighs, so these MUST
    # still collapse — the fix must not shatter the cache into singletons.
    ("unsalted butter, softened", "butter (softened)"),
    ("large free-range eggs", "eggs"),
    ("freshly chopped parsley", "parsley"),
    ("2 cups plain flour", "2 cups flour"),
])
def test_preparation_and_grade_still_share_a_cache_key(a, b):
    assert food_term(a) == food_term(b) and food_term(a)


def test_a_food_term_is_never_empty_for_real_text():
    assert food_term("peanut butter")
    assert food_term("") == ""


@pytest.mark.parametrize("text, head_density", [
    ("hazelnut butter", 0.96),   # butter
    ("pecan butter", 0.96),
    ("walnut oil", 0.92),        # oil
])
def test_an_untabled_compound_does_not_inherit_across_the_boundary(text, head_density):
    """The guard, tested where it is the ONLY thing acting.

    Foods with a real entry in the density table (peanut butter, rice vinegar)
    cannot test this: they would pass with the guard deleted. These have no
    entry, so inheriting the head's value is the only thing that could happen if
    the boundary check were removed.
    """
    got = units._density_for(text)
    assert got != head_density, (
        f"{text!r} inherited its head noun's density {head_density}")
    assert got is None, f"{text!r} should have no density, got {got}"


@pytest.mark.parametrize("text, expected", [
    # These DO have real entries, and the point is that the entries are their
    # own measured values rather than the head noun's.
    ("peanut butter", 1.09),
    ("condensed milk", 1.28),
    ("rice vinegar", 1.01),
])
def test_a_compound_uses_its_own_measured_density(text, expected):
    assert units._density_for(text) == expected


@pytest.mark.parametrize("text, expected", [
    # ...while a qualifier that does NOT change the substance still inherits,
    # or the table would need a row per adjective.
    ("sesame oil", 0.92),
    ("sunflower oil", 0.92),
    ("maple syrup", 1.37),
    ("chicken stock", 1.0),
])
def test_a_harmless_qualifier_still_inherits_the_head_density(text, expected):
    assert units._density_for(text) == expected


def test_a_table_key_is_reachable_under_its_canonical_spelling():
    """"yogurt" sat in the table while lookups canonicalised to "yoghurt", so
    the row existed and could never be hit."""
    assert units._density_for("yoghurt") == units._density_for("yogurt") == 1.03


@pytest.mark.parametrize("text, expected", [
    ("butter", 0.96),
    ("2 cups milk", 1.03),
    ("olive oil", 0.92),
    ("plain flour", 0.53),
])
def test_the_shipped_densities_still_resolve(text, expected):
    """The boundary must not be so strict that known foods stop being found."""
    assert units._density_for(text) == expected


def test_to_grams_uses_the_right_density_for_a_compound():
    """End to end: the number a user actually sees in the weight view."""
    plain = units.to_grams("1 cup rice")
    vinegar = units.to_grams("1 cup rice vinegar")
    assert plain is not None
    assert vinegar != plain, "rice vinegar was weighed as rice"


def test_a_learned_weight_for_butter_is_not_served_for_peanut_butter():
    """The full reported failure, through the public entry point."""
    learned = {("stick", food_term("butter")): 113.0}

    def lookup(unit, rest):
        return learned.get((unit, food_term(rest)))

    assert units.to_grams("2 sticks butter", learned=lookup) == pytest.approx(226.0)
    assert units.to_grams("2 sticks peanut butter", learned=lookup) is None
