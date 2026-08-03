"""Reading an oven temperature out of recipe text.

The hard half is NOT reading numbers that merely look like temperatures — a
recipe is full of gram weights, minutes and tin sizes.
"""
import pytest

from app.services.cooking import (
    c_to_f,
    f_to_c,
    format_temperature,
    parse_temperature,
)


@pytest.mark.parametrize("text,celsius", [
    ("Preheat the oven to 350°F.", 176.67),
    ("bake at 350 °f", 176.67),
    ("Heat to 425 F", 218.33),
    ("Bake at 180C for 20 minutes", 180),
    ("Preheat oven to 200 degrees C", 200),
    ("Preheat the oven to 220 degrees celsius", 220),
    ("Cook at gas mark 4", 180),
    ("Preheat to 180°C (gas mark 4)", 180),
    ("gas mark 6 (400°F)", 200),
])
def test_a_stated_temperature_is_read(text, celsius):
    assert parse_temperature(text) == pytest.approx(celsius, abs=0.5)


@pytest.mark.parametrize("text", [
    "See page 350 for the sauce",
    "Add 350 g flour",
    "Cook for 350 minutes",
    "Serves 4",
    "Bake at 350",                 # no unit: US means F, Europe means C
    "Add 200 ml water",
    "Stir in 100 g butter",
    "",
])
def test_a_number_that_is_not_a_temperature_is_ignored(text):
    """The false-positive set is the important half: a recipe is full of
    numbers, and a wrong oven temperature is worse than none. A bare number
    with no unit is genuinely ambiguous — US recipes mean Fahrenheit and
    European ones Celsius — so it is left alone rather than guessed."""
    assert parse_temperature(text) is None


@pytest.mark.parametrize("text", [
    "Chill at 4 degrees C",        # a fridge, not an oven
    "Preheat to 900 C",            # a kiln
])
def test_an_implausible_oven_temperature_is_rejected(text):
    assert parse_temperature(text) is None


def test_the_first_temperature_wins():
    """Recipes state the oven temperature once, up front; a later "reduce to
    160°C" must not overwrite what the cook preheats to."""
    assert parse_temperature(
        "Preheat to 220°C. Bake 10 minutes, then reduce to 160°C.") == 220


def test_a_fahrenheit_recipe_still_reads_back_as_the_number_it_stated():
    """350°F is 176.67°C. Storing a rounded 177 and converting back gives
    351°F — the recipe's own number would stop matching what we show."""
    stored = parse_temperature("Preheat the oven to 350°F")

    assert "350°F" in format_temperature(stored)


def test_display_rounds_to_something_an_oven_dial_has():
    assert format_temperature(180) == "180°C / 355°F"
    assert format_temperature(None) == ""
    assert format_temperature(0) == ""


def test_conversions_round_trip():
    assert c_to_f(f_to_c(350)) == pytest.approx(350)


# --- Wiring: the two places a new field silently disappears ----------------

def test_a_temperature_survives_a_version_restore(auth_client):
    """A field the update endpoint accepts but the SNAPSHOT BUILDER omits is
    silently wiped when a version is restored. That has bitten this repo
    before, so cookTemperatureC being in _snapshot_recipe is pinned here."""
    rid = auth_client.post("/api/v1/recipes",
                           json={"name": "Roast", "cookTemperatureC": 200}).get_json()["id"]
    auth_client.put(f"/api/v1/recipes/{rid}", json={"description": "v2"})
    versions = auth_client.get(f"/api/v1/recipes/{rid}/versions").get_json()
    vid = (versions[0] if isinstance(versions, list) else versions["items"][0])["id"]
    auth_client.put(f"/api/v1/recipes/{rid}", json={"cookTemperatureC": 120})

    auth_client.post(f"/api/v1/recipes/{rid}/versions/{vid}/restore")

    after = auth_client.get(f"/api/v1/recipes/{rid}").get_json()
    assert after["cookTemperatureC"] == 200


def test_an_imported_recipe_gets_its_temperature_from_the_steps(auth_client):
    """The end-to-end point of the change: schema.org has no temperature field,
    so it has to come out of the instruction text."""
    from app.services.ai.recipe_import import normalize_jsonld

    payload = normalize_jsonld({
        "name": "Roast chicken",
        "recipeIngredient": ["1 chicken"],
        "recipeInstructions": [
            {"@type": "HowToStep", "text": "Preheat the oven to 425°F."},
            {"@type": "HowToStep", "text": "Roast for 90 minutes."},
        ],
    })

    assert payload["cookTemperatureC"] == pytest.approx(218.33, abs=0.5)


def test_a_recipe_with_no_temperature_imports_as_none(auth_client):
    from app.services.ai.recipe_import import normalize_jsonld

    payload = normalize_jsonld({
        "name": "Salad",
        "recipeInstructions": [{"@type": "HowToStep", "text": "Toss and serve."}],
    })

    assert payload["cookTemperatureC"] is None


def test_the_serializer_exposes_both_units(auth_client):
    rid = auth_client.post("/api/v1/recipes",
                           json={"name": "Bake", "cookTemperatureC": 176.67}).get_json()["id"]

    out = auth_client.get(f"/api/v1/recipes/{rid}").get_json()

    assert out["cookTemperature"] == "175°C / 350°F"
