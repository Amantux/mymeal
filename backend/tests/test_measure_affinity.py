"""`units.measures_for` — which units a food is prepopulated with.

The invariant worth protecting is not any single food's default; it is that the
WEIGHT BIAS has the two exemptions it is supposed to have. A later change that
"makes the bias consistent" by dropping them would produce "2 g dried basil"
and "250 g water", which is why each exemption gets its own named test rather
than riding along in a table.
"""
from __future__ import annotations

import pytest

from app.services import units


def test_a_dry_bulk_good_defaults_to_grams_because_a_cup_is_a_scoop():
    affinity = units.measures_for("flour")

    assert affinity.preferred == "g"
    assert affinity.dimension == "weight"


def test_a_countable_food_defaults_to_a_bare_count_not_a_weight():
    affinity = units.measures_for("oranges")

    assert affinity.preferred == ""
    assert affinity.dimension == "count"


@pytest.mark.parametrize("spice", ["cinnamon", "nutmeg", "paprika"])
def test_a_spice_prefers_a_spoon_because_a_scale_cannot_resolve_one_gram(spice):
    """The weight bias must NOT reach here.

    A 2 g target on a ±1 g scale is a 50% error, so a teaspoon is the more
    precise instrument as well as the more readable one. This is the exact
    opposite of the flour case and the reason the bias is applied by kind.
    """
    affinity = units.measures_for(spice)

    assert affinity.preferred == "tsp"
    assert affinity.dimension == "small_volume"


@pytest.mark.parametrize("liquid", ["water", "milk", "olive oil"])
def test_a_natively_liquid_food_prefers_volume(liquid):
    """Also exempt: consistent density means a millilitre is already accurate,
    so converting to grams costs readability and buys nothing."""
    affinity = units.measures_for(liquid)

    assert affinity.dimension == "volume"


def test_a_mass_noun_vegetable_is_weighed_rather_than_counted():
    """Classified vegetable, and still not countable — nobody buys
    "2 spinaches"."""
    affinity = units.measures_for("spinach")

    assert affinity.dimension == "weight"


def test_an_unknown_food_returns_none_rather_than_guessing_a_unit():
    """Fail open, like the rest of this module.

    A prepopulated unit is a recommendation, so a wrong one is worse than
    none — the caller offers the full list instead.
    """
    assert units.measures_for("unicorn steak") is None
    assert units.measures_for("") is None


def test_volume_units_remain_offered_on_a_weight_biased_food():
    """The bias sets the DEFAULT; it does not remove the choice.

    A cook holding a recipe written in cups has to be able to enter cups, so
    this asserts the escape hatch the bias must never close.
    """
    affinity = units.measures_for("flour")

    assert "cup" in affinity.acceptable
    assert affinity.acceptable[0] == affinity.preferred


def test_every_offered_unit_is_one_the_parser_actually_knows():
    """A prepopulated unit the parser cannot read back would be a dead end.

    Checked for every classification rather than a sample, because the offers
    are hand-written per dimension and a typo there is invisible until a user
    picks that unit.
    """
    for food in ("flour", "oranges", "water", "cinnamon"):
        affinity = units.measures_for(food)
        for unit in affinity.acceptable:
            if unit == "":  # the bare count
                continue
            assert units.canonical_unit(unit) == unit, (
                f"{unit!r} offered for {food!r} is not a canonical unit")


# --- the household weight bias, end to end ---------------------------------

def test_a_recipe_leads_with_weight_by_default(auth_client):
    """The bias is the DEFAULT, applied with no ?units= on the request.

    Asserted through the route rather than the service because the default is
    exactly the thing a service-level test would miss: the old code read the
    query arg and nothing else, and a service test would have passed either
    way.
    """
    created = auth_client.post("/api/v1/recipes", json={
        "title": "Bias check", "servings": 2,
        "ingredients": [{"display": "1 cup flour"}],
    })
    assert created.status_code in (200, 201), created.get_json()
    recipe_id = created.get_json()["id"]

    body = auth_client.get(f"/api/v1/recipes/{recipe_id}").get_json()

    line = body["ingredients"][0]["display"]
    assert "125 g" in line, line
    # The written measure SURVIVES. This is what makes flipping the default
    # safe — a household without a scale can ignore the parenthetical, and
    # nothing was rewritten or lost.
    assert "cup" in line, line


def test_as_written_opts_out_of_the_weight_annotation(auth_client):
    created = auth_client.post("/api/v1/recipes", json={
        "title": "Opt out", "servings": 2,
        "ingredients": [{"display": "1 cup flour"}],
    })
    recipe_id = created.get_json()["id"]
    auth_client.put("/api/v1/preferences", json={"measureBias": "as_written"})

    body = auth_client.get(f"/api/v1/recipes/{recipe_id}").get_json()

    assert "125 g" not in body["ingredients"][0]["display"]


def test_an_explicit_units_param_overrides_the_household_default(auth_client):
    """So a client is never locked out of the other rendering."""
    created = auth_client.post("/api/v1/recipes", json={
        "title": "Override", "servings": 2,
        "ingredients": [{"display": "1 cup flour"}],
    })
    recipe_id = created.get_json()["id"]
    auth_client.put("/api/v1/preferences", json={"measureBias": "as_written"})

    body = auth_client.get(
        f"/api/v1/recipes/{recipe_id}?units=weight").get_json()

    assert "125 g" in body["ingredients"][0]["display"]


def test_the_measures_endpoint_prepopulates_and_fails_open(auth_client):
    known = auth_client.get("/api/v1/measures?food=flour").get_json()
    assert known["known"] is True
    assert known["preferred"] == "g"
    assert "cup" in known["acceptable"]
    assert known["reason"]

    # A bare count serialises as null, not "" — "" would read as a unit whose
    # name happens to be empty.
    counted = auth_client.get("/api/v1/measures?food=oranges").get_json()
    assert counted["preferred"] is None
    assert counted["dimension"] == "count"

    unknown = auth_client.get("/api/v1/measures?food=unicorn+steak").get_json()
    assert unknown["known"] is False
    assert unknown["acceptable"] == []

    assert auth_client.get("/api/v1/measures").status_code == 400


# --- what the default-on bias exposed --------------------------------------

def test_a_line_already_measured_by_weight_is_not_annotated_again():
    """Regression: "240 grams Egg Whites(240 g) (240 g)".

    Latent for as long as the weight view was an opt-in toggle people used on
    volume-written recipes. Making weight the DEFAULT pointed it at every
    gram-measured ingredient in the app, which is how a cosmetic bug became a
    universal one — worth recording, because that is the general hazard of
    flipping a default rather than anything specific to grams.
    """
    assert units.annotate_weight("240 grams egg whites") == "240 grams egg whites"
    assert units.annotate_weight("2.5 kg potatoes") == "2.5 kg potatoes"
    assert units.annotate_weight("8 oz butter") == "8 oz butter"

    # Still annotates what it should.
    assert "(125 g)" in units.annotate_weight("1 cup flour")


@pytest.mark.parametrize("line", ["1 tsp cinnamon", "1 tbsp paprika"])
def test_a_spice_line_is_not_annotated_with_an_unusable_gram_figure(line):
    """The annotation defers to measures_for rather than re-deciding.

    Two places answering "is weight the better instrument for this food" is
    how they come to disagree, so this asserts the deferral and not just the
    outcome.
    """
    assert units.annotate_weight(line) == line
